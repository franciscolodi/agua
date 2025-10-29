# -*- coding: utf-8 -*-
# Agua / Estación — reporte diario 8→8 con downsampling 30min

import os, sys, time, statistics, datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytz
import requests
import numpy as np
import matplotlib
matplotlib.use("Agg")  # backend no interactivo para servidores/CI
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
from scipy.ndimage import gaussian_filter1d

# =========================
# 🌎 CONFIGURACIÓN GLOBAL
# =========================
TZ = pytz.timezone("America/Santiago")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IO_USERNAME = os.getenv("IO_USERNAME")
IO_KEY = os.getenv("IO_KEY")
if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, IO_USERNAME, IO_KEY]):
    sys.exit("❌ ERROR: Faltan variables de entorno necesarias.")

BASE_URL = f"https://io.adafruit.com/api/v2/{IO_USERNAME}/feeds"

# Claves EXACTAS en IO (ver captura)
FEEDS = [
    "estacion.temperatura",
    "estacion.humedad",
    "estacion.presion",
    "estacion.altitud",
    "estacion-dot-punto-rocio",
    "estacion-dot-sensacion-termica",
    "estacion-dot-densidad-aire",
    "estacion-dot-humedad-suelo",
    "estacion.luz",
    "estacion.rele_control",  # verifica esta key en IO; si es 'estacion-dot-rele-control', cámbiala
]

UNIDADES = {
    "temperatura": "°C",
    "humedad": "%",
    "presion": "hPa",
    "altitud": "m",
    "punto_rocio": "°C",
    "sensacion_termica": "°C",
    "densidad_aire": "kg/m³",
    "humedad_suelo": "%",
    "luz": "lux",
    "rele_control": "",
}

# =========================
# 🔧 HTTP ROBUSTO
# =========================
SESSION = requests.Session()
SESSION.headers.update({"X-AIO-Key": IO_KEY})

def safe_get(url, params, retries=4, backoff=1.5, timeout=20):
    for i in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            # rate limit / server hiccups
            if r.status_code in (429, 500, 502, 503, 504):
                sleep_s = backoff ** i
                print(f"⏳ {r.status_code} reintento {i+1}/{retries} en {sleep_s:.1f}s")
                time.sleep(sleep_s)
                continue
            print(f"⚠️ HTTP {r.status_code}: {r.text[:120]}")
            return []
        except requests.RequestException as e:
            sleep_s = backoff ** i
            print(f"⚠️ Excepción {e} (retry {i+1}) en {sleep_s:.1f}s")
            time.sleep(sleep_s)
    return []

# ========= FETCH con paginación (obligatorio para >1000 puntos) =========
def fetch_feed(feed_key, start_iso, end_iso):
    """Descarga TODOS los datos del feed entre start/end, paginando de 1000 en 1000."""
    url = f"{BASE_URL}/{feed_key}/data"
    limit = 1000
    page = 1
    out = []
    while True:
        params = {"start_time": start_iso, "end_time": end_iso, "limit": limit, "page": page}
        batch = safe_get(url, params)
        print(f"📡 {feed_key} page={page} -> {len(batch)}")
        if not batch:
            break
        out.extend(batch)
        if len(batch) < limit:
            break
        page += 1
    return out


# =========================
# 🧮 PARSING & DOWNSAMPLING
# =========================
def parse_raw(data):
    """[(t_local, valor_float)] ordenada; detecta si created_at viene en UTC o local."""
    parsed = []
    for d in data or []:
        try:
            v = float(d["value"])
            t_raw = d["created_at"]
            if t_raw.endswith("-03:00"):
                # Ya viene en hora Chile
                t = dt.datetime.fromisoformat(t_raw)
            elif "Z" in t_raw or t_raw.endswith("+00:00"):
                # UTC → Chile
                t = dt.datetime.fromisoformat(t_raw.replace("Z", "+00:00")).astimezone(TZ)
            else:
                # Sin zona → asume local
                t = dt.datetime.fromisoformat(t_raw)
                if t.tzinfo is None:
                    t = TZ.localize(t)
            parsed.append((t, v))
        except Exception:
            continue
    return sorted(parsed, key=lambda x: x[0])




# ========= Downsampling exacto a 30 min (O(n)) =========
def downsample_30min(values, start_local, end_local, tolerance_minutes=15):
    """
    Un punto por marca de 30 min (HH:00/HH:30) en [start_local, end_local].
    Toma el valor más cercano dentro de ±tolerance.
    """
    if not values:
        return []
    step = dt.timedelta(minutes=30)
    tol = dt.timedelta(minutes=tolerance_minutes)

    # Construir marcas exactas cada 30'
    slots = []
    t = start_local
    while t <= end_local:
        slots.append(t)
        t += step

    result, idx, n = [], 0, len(values)
    for slot in slots:
        best, best_delta = None, tol
        while idx < n:
            vt, vv = values[idx]
            delta = vt - slot
            if delta > tol:
                break
            if abs(delta) <= tol and abs(delta) < best_delta:
                best = (slot, vv)  # fijamos el timestamp EXACTO de la marca
                best_delta = abs(delta)
            idx += 1
        if best:
            result.append(best)
    return result
def calc_stats(values):
    if not values: return None
    arr = [v for _, v in values]
    return {
        "n": len(arr),
        "min": min(arr),
        "max": max(arr),
        "mean": statistics.fmean(arr),
        "std": statistics.pstdev(arr) if len(arr) > 1 else 0.0,
        "first": arr[0],
        "last": arr[-1],
    }

def trend_symbol(first, last, threshold=0.1):
    if first is None or last is None: return "→"
    delta = last - first
    if abs(delta) < threshold: return "→"
    return "↑" if delta > 0 else "↓"

# =========================
# 🎨 GRÁFICOS
# =========================
def norm_key_for_units(feed):
    key = (feed.replace("estacion.", "")
               .replace("estacion-dot-", "")
               .strip())
    return key.replace(" ", "_")

def pretty_title(feed):
    return (feed.replace("estacion.", "")
                .replace("estacion-dot-", "")
                .replace("_", " ")
                .title())

# ========= Gráfico (48h) =========
def make_plot(feed, values, start_local, end_local):
    """Gráfico temporal (48h, marcas 30’), unidad en Y y tz local en ticks."""
    if not values:
        return None

    x, y = zip(*values)
    y = np.asarray(y, dtype=float)
    y_smooth = gaussian_filter1d(y, sigma=1.1)

    key_norm = norm_key_for_units(feed)
    unidad = UNIDADES.get(key_norm, "")

    fig, ax = plt.subplots(figsize=(7.8, 3.2), dpi=130)
    ax.plot(x, y_smooth, linewidth=1.8, alpha=0.9)

    ax.set_title(pretty_title(feed), fontsize=12, fontweight="bold", pad=8)
    ax.set_xlabel("Hora", fontsize=9)
    ax.set_ylabel(f"Valor {unidad}", fontsize=9)

    # Ticks X cada 4 horas (48h total → ~12 etiquetas)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %H:%M", tz=TZ))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=4, tz=TZ))
    ax.set_xlim(start_local, end_local)
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center", fontsize=8)

    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=6))
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.35)

    # líneas verticales en las 08:00 para separar los días
    day1 = start_local
    day2 = start_local + dt.timedelta(days=1)
    for vline in (day1, day2, end_local):
        ax.axvline(vline, color="gray", linestyle=":", alpha=0.6, linewidth=0.9)

    ax.margins(x=0.01, y=0.06)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    plt.tight_layout(pad=1.0)

    path = Path(f"/tmp/{feed}.png")
    plt.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)

# ========= MAIN: ventana de 2 días (48h) 08:00 → 08:00 de hoy =========
def main():
    now_local = dt.datetime.now(TZ)
    today_8am = now_local.replace(hour=8, minute=0, second=0, microsecond=0)

    # Siempre una ventana de 48h ya finalizada: anteayer 08:00 → hoy 08:00
    if now_local >= today_8am:
        start_local = today_8am - dt.timedelta(days=2)
        end_local   = today_8am
    else:
        start_local = today_8am - dt.timedelta(days=3)
        end_local   = today_8am - dt.timedelta(days=1)

    # A la API: UTC (Adafruit IO trabaja en UTC); al graficar: local
    start_iso = start_local.astimezone(pytz.UTC).isoformat()
    end_iso   = end_local.astimezone(pytz.UTC).isoformat()

    print(f"📆 Ventana local (48h): {start_local:%d-%b %H:%M} → {end_local:%d-%b %H:%M}")
    print(f"🌐 Ventana UTC        : {start_iso} → {end_iso}")

    summary = [
        f"*📊 Reporte estación 48h ({end_local:%Y-%m-%d %H:%M})*",
        f"🕗 Intervalo: {start_local:%d-%b %H:%M} → {end_local:%d-%b %H:%M}",
        ""
    ]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_feed, feed, start_iso, end_iso): feed for feed in FEEDS}

        for future in as_completed(futures):
            feed = futures[future]
            try:
                data = future.result()
                raw_vals = parse_raw(data)

                # Verificación rápida (te ayuda a chequear cortes)
                if raw_vals:
                    print(f"🧪 {feed}: raw {raw_vals[0][0]} → {raw_vals[-1][0]} (n={len(raw_vals)})")

                valores = downsample_30min(raw_vals, start_local, end_local, tolerance_minutes=15)

                st = calc_stats(valores)
                key_title = pretty_title(feed)
                key_norm  = norm_key_for_units(feed)
                suf       = UNIDADES.get(key_norm, "")

                if not st:
                    summary.append(f"• `{key_title}`: sin datos 📭")
                    continue

                trend = trend_symbol(st["first"], st["last"])

                if "rele" in key_norm:
                    estado = "💧 Riego ACTIVADO" if st["last"] >= 1 else "💤 Riego APAGADO"
                    summary.append(f"• `{key_title}` → {estado}")
                else:
                    summary.append(
                        f"• `{key_title}` (48h) → n={st['n']}, "
                        f"min={st['min']:.2f}{suf}, max={st['max']:.2f}{suf}, "
                        f"media={st['mean']:.2f}{suf}, σ={st['std']:.2f}, tendencia {trend}"
                    )

                img = make_plot(feed, valores, start_local, end_local)
                if img:
                    telegram_send_photo(img, caption=f"{key_title} 48h ({suf})")

            except Exception as e:
                summary.append(f"• `{feed}`: ⚠️ Error {e}")

    telegram_send_text("\n".join(summary))
    print("✅ Reporte 48h enviado con éxito.")
# =========================
# 📲 TELEGRAM
# =========================
def telegram_send_text(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=15)
    except requests.RequestException as e:
        print(f"⚠️ Telegram text error: {e}")

def telegram_send_photo(path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    try:
        with open(path, "rb") as f:
            requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"photo": f}, timeout=30)
    except requests.RequestException as e:
        print(f"⚠️ Telegram photo error: {e}")

# =========================
# 🚀 MAIN: 8→8 últimas 24h (cerradas)
# =========================
def main():
    now_local = dt.datetime.now(TZ)
    today_8am = now_local.replace(hour=8, minute=0, second=0, microsecond=0)

    # Siempre un bloque 8→8 ya concluido
    if now_local >= today_8am:
        start_local = today_8am - dt.timedelta(days=1)
        end_local = today_8am
    else:
        start_local = today_8am - dt.timedelta(days=2)
        end_local = today_8am - dt.timedelta(days=1)

    start_iso = start_local.astimezone(pytz.UTC).isoformat()
    end_iso   = end_local.astimezone(pytz.UTC).isoformat()


    print(f"📆 Rango local: {start_local:%d-%b %H:%M} → {end_local:%d-%b %H:%M}")
    print(f"🌐 Rango UTC  : {start_iso} → {end_iso}")

    summary = [
        f"*📊 Reporte estación ({end_local:%Y-%m-%d %H:%M})*",
        f"🕗 Intervalo: {start_local:%d-%b %H:%M} → {end_local:%d-%b %H:%M}",
        ""
    ]

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_feed, feed, start_iso, end_iso): feed for feed in FEEDS}

        for future in as_completed(futures):
            feed = futures[future]
            try:
                data = future.result()
                raw_vals = parse_raw(data)
                # Alinear a marcas exactas del rango (8:00, 8:30, …)
                valores = downsample_30min(raw_vals, start_local, end_local, tolerance_minutes=15)

                st = calc_stats(valores)
                if not st:
                    summary.append(f"• `{feed}`: sin datos 📭")
                    continue

                key_title = pretty_title(feed)
                trend = trend_symbol(st["first"], st["last"])
                key_norm = norm_key_for_units(feed)
                suf = UNIDADES.get(key_norm, "")

                # Riego (binario)
                if "rele" in key_norm:
                    estado = "💧 Riego ACTIVADO" if st["last"] >= 1 else "💤 Riego APAGADO"
                    summary.append(f"• `{key_title}` → {estado}")
                else:
                    summary.append(
                        f"• `{key_title}` → n={st['n']}, "
                        f"min={st['min']:.2f}{suf}, max={st['max']:.2f}{suf}, "
                        f"media={st['mean']:.2f}{suf}, σ={st['std']:.2f}, tendencia {trend}"
                    )

                img = make_plot(feed, valores, start_local, end_local)
                if img:
                    telegram_send_photo(img, caption=f"{key_title} ({suf})")

            except Exception as e:
                summary.append(f"• `{feed}`: ⚠️ Error {e}")

    telegram_send_text("\n".join(summary))
    print("✅ Reporte diario enviado con éxito.")

if __name__ == "__main__":
    main()
