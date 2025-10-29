import os, sys, pytz, requests, statistics, datetime as dt, time
import matplotlib.pyplot as plt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================================================
# 🌎 CONFIGURACIÓN GLOBAL
# =========================================================
TZ = pytz.timezone("America/Santiago")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IO_USERNAME = os.getenv("IO_USERNAME")
IO_KEY = os.getenv("IO_KEY")
BASE_URL = f"https://io.adafruit.com/api/v2/{IO_USERNAME}/feeds"

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
    "estacion.rele_control"
]

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, IO_USERNAME, IO_KEY]):
    sys.exit("❌ ERROR: Faltan variables de entorno necesarias.")

# =========================================================
# 🔧 UTILIDADES HTTP
# =========================================================
def safe_request(url, headers, params):
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        print(f"⚠️ Error HTTP {r.status_code} → {url}")
    except Exception as e:
        print(f"⚠️ Excepción en request: {e}")
    return []

def fetch_feed(feed_key, start_iso, end_iso):
    """Descarga datos del feed (24 h ≤ 1000 pts)."""
    url = f"{BASE_URL}/{feed_key}/data"
    headers = {"X-AIO-Key": IO_KEY}
    params = {"start_time": start_iso, "end_time": end_iso, "limit": 1000}
    return safe_request(url, headers, params)

# =========================================================
# 🧮 PARSING Y DOWNSAMPLING EXACTO CADA 30 MIN
# =========================================================
def parse_raw(data):
    """Convierte a lista [(t_local, valor_float)] ordenada por tiempo."""
    parsed = []
    for d in data:
        try:
            v = float(d["value"])
            t = dt.datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(TZ)
            parsed.append((t, v))
        except Exception:
            continue
    return sorted(parsed, key=lambda x: x[0])

def downsample_30min(values, start_local, end_local, tolerance_minutes=10):
    """
    Alinea los datos a ranuras de 30 minutos: HH:00 y HH:30.
    Para cada ranura toma el punto más cercano dentro de ±tolerance_minutes.
    Si no hay datos cercanos, omite la ranura.
    """
    if not values:
        return []

    # índices temporales destino
    slots = []
    t = start_local.replace(minute=0, second=0, microsecond=0)
    # Si el inicio es 08:00 exacto, perfecto; si no, ajustamos al múltiplo más cercano
    if start_local.minute >= 30:
        t = t.replace(hour=start_local.hour, minute=30)
    else:
        t = t.replace(hour=start_local.hour, minute=0)

    while t <= end_local:
        slots.append(t)
        t += dt.timedelta(minutes=30)

    # puntero de valores
    vi = 0
    out = []
    tol = dt.timedelta(minutes=tolerance_minutes)

    for slot in slots:
        best = None
        best_dt = None
        # avanzar mientras no nos pasemos del slot+tol
        while vi < len(values):
            vt, vv = values[vi]
            if vt < slot - tol:
                vi += 1
                continue
            if vt > slot + tol:
                break
            # candidato dentro de tolerancia
            delta = abs(vt - slot)
            if (best_dt is None) or (delta < best_dt):
                best_dt = delta
                best = (vt, vv)
            vi += 1
        if best:
            out.append(best)

    # asegurar extremos si existen
    if out and out[0][0] > start_local and (values[0][0] - start_local) <= tol:
        out = [values[0]] + out
    if out and out[-1][0] < end_local and (end_local - values[-1][0]) <= tol:
        out = out + [values[-1]]

    return out

def calc_stats(values):
    if not values: return None
    arr = [v for _, v in values]
    return dict(
        n=len(arr),
        min=min(arr),
        max=max(arr),
        mean=statistics.fmean(arr),
        std=statistics.pstdev(arr) if len(arr) > 1 else 0,
        first=arr[0],
        last=arr[-1]
    )

def trend_symbol(first, last, threshold=0.1):
    if first is None or last is None: return "→"
    delta = last - first
    if abs(delta) < threshold: return "→"
    return "↑" if delta > 0 else "↓"

# =========================================================
# 🎨 GRÁFICO
# =========================================================
import matplotlib.dates as mdates
import matplotlib.ticker as ticker
from scipy.ndimage import gaussian_filter1d
import numpy as np

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

def norm_key_for_units(feed):
    """Normaliza el nombre para buscar la unidad en UNIDADES."""
    key = (feed.replace("estacion.", "")
               .replace("estacion-dot-", "")
               .strip())
    return key.replace(" ", "_")  # ← clave para UNIDADES

def pretty_title(feed):
    return (feed.replace("estacion.", "")
                .replace("estacion-dot-", "")
                .replace("_", " ")
                .title())

def make_plot(feed, values, start_local, end_local):
    """Gráfico temporal (8–8, 30min), con unidad en eje Y y TZ local en ticks."""
    if not values:
        return None

    x, y = zip(*values)
    y = np.array(y, dtype=float)
    y_smooth = gaussian_filter1d(y, sigma=1.2)

    key_norm = norm_key_for_units(feed)
    unidad = UNIDADES.get(key_norm, "")

    fig, ax = plt.subplots(figsize=(6.8, 3.2), dpi=130)
    ax.plot(x, y_smooth, linewidth=1.8, color="#007ACC", alpha=0.9)

    ax.set_title(pretty_title(feed), fontsize=12, fontweight="bold", pad=8)
    ax.set_xlabel("Hora", fontsize=9)
    ax.set_ylabel(f"Valor {unidad}", fontsize=9)

    # Eje X: ticks cada 2h, con TZ local
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=TZ))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2, tz=TZ))
    ax.set_xlim(start_local, end_local)  # ← fija 8→8 exacto
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center", fontsize=8)

    # Y limpio
    ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=6))
    ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.4)

    ax.set_facecolor("white")
    fig.patch.set_facecolor("white")
    ax.margins(x=0.02, y=0.06)
    plt.tight_layout(pad=1.2)

    path = Path(f"/tmp/{feed}.png")
    plt.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)

# =========================================================
# 📲 TELEGRAM
# =========================================================
def telegram_send_text(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})

def telegram_send_photo(path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"photo": f})

# =========================================================
# 🚀 PROCESO PRINCIPAL (PARALELO) - INTERVALO 8:00 a 8:00
# =========================================================
def main():
    now_local = dt.datetime.now(TZ)

    # --- Calcular rango horario real de las últimas 24 h (8 → 8) ---
    today_8am = now_local.replace(hour=8, minute=0, second=0, microsecond=0)
    
    if now_local >= today_8am:
        # Estamos después de las 8 am → usar ayer 08:00 → hoy 08:00
        start_local = today_8am - dt.timedelta(days=1)
        end_local = today_8am
    else:
        # Antes de las 8 am → usar anteayer 08:00 → ayer 08:00
        start_local = today_8am - dt.timedelta(days=2)
        end_local = today_8am - dt.timedelta(days=1)
    # API en UTC
    start_iso = start_local.isoformat()
    end_iso   = end_local.isoformat()


    print(f"📆 Rango temporal: {start_local:%d-%b %H:%M} → {end_local:%d-%b %H:%M}")

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
                valores = downsample_30min(raw_vals, start_local, end_local, tolerance_minutes=10)

                st = calc_stats(valores)
                if not st:
                    summary.append(f"• `{feed}`: sin datos 📭")
                    continue

                key_title = pretty_title(feed)
                trend = trend_symbol(st["first"], st["last"])

                # Riego
                if "rele" in feed:
                    estado = "💧 Riego ACTIVADO" if st["last"] >= 1 else "💤 Riego APAGADO"
                    summary.append(f"• `{key_title}` → {estado}")
                    continue

                # Unidades para el resumen (arreglo del bug)
                key_norm = norm_key_for_units(feed)
                suf = UNIDADES.get(key_norm, "")

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
