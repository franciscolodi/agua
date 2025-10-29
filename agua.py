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
# 🔧 FUNCIONES UTILITARIAS
# =========================================================
def safe_request(url, headers, params):
    try:
        r = requests.get(url, headers=headers, params=params, timeout=15)
        if r.status_code == 200:
            return r.json()
        print(f"⚠️ Error HTTP {r.status_code}")
    except Exception as e:
        print(f"⚠️ Excepción en request: {e}")
    return []

def fetch_feed(feed_key, start, end):
    """Descarga datos del feed sin paginar (24 h ≈ 1000 pts)"""
    url = f"{BASE_URL}/{feed_key}/data"
    headers = {"X-AIO-Key": IO_KEY}
    params = {"start_time": start, "end_time": end, "limit": 1000}
    return safe_request(url, headers, params)

def parse_feed_data(data, interval_minutes=30):
    """Convierte y reduce datos a un punto cada 'interval_minutes'."""
    parsed = []
    for d in data:
        try:
            v = float(d["value"])
            t = dt.datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(TZ)
            parsed.append((t, v))
        except:
            continue
    parsed = sorted(parsed, key=lambda x: x[0])
    # reducir datos: un punto cada 30 minutos aprox
    step = max(1, len(parsed) // (24 * 60 // interval_minutes))
    return parsed[::step]

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

import matplotlib.dates as mdates

def make_plot(feed, values):
    """Genera gráfico temporal con eje X por hora y estilo legible."""
    if not values:
        return None

    x, y = zip(*values)

    fig, ax = plt.subplots(figsize=(6.5, 3.2), dpi=130)
    ax.plot(x, y, linewidth=1.8, color="#0072B2", alpha=0.9)

    # Título mejorado
    titulo = feed.replace("estacion.", "").replace("estacion-dot-", "").replace("_", " ").title()
    ax.set_title(titulo, fontsize=11, fontweight="bold", pad=10)

    # Etiquetas
    ax.set_xlabel("Hora", fontsize=9)
    ax.set_ylabel("Valor", fontsize=9)

    # Eje X formateado en horas
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right", fontsize=8)

    # Eje Y con cuadrícula suave
    ax.grid(alpha=0.3, linestyle="--")

    # Márgenes visuales
    ax.margins(x=0.02, y=0.1)
    plt.tight_layout()

    # Guardar
    path = Path(f"/tmp/{feed}.png")
    plt.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)



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

    # --- Definir rango horario de 24h desde las 8:00 ---
    today_8am = now_local.replace(hour=8, minute=0, second=0, microsecond=0)
    if now_local.hour < 8:
        # Si todavía no son las 8, usamos el ciclo anterior
        end_local = today_8am
        start_local = today_8am - dt.timedelta(days=1)
    else:
        # Si ya pasaron las 8, usamos el ciclo actual
        start_local = today_8am
        end_local = today_8am + dt.timedelta(days=1)

    # --- Convertir a UTC para Adafruit IO ---
    start = start_local.astimezone(pytz.UTC).isoformat()
    end = end_local.astimezone(pytz.UTC).isoformat()

    print(f"📆 Rango temporal: {start_local:%d-%b %H:%M} → {end_local:%d-%b %H:%M}")

    # --- Encabezado del resumen ---
    summary = [
        f"*📊 Reporte estación ({end_local:%Y-%m-%d %H:%M})*",
        f"🕗 Intervalo: {start_local:%d-%b %H:%M} → {end_local:%d-%b %H:%M}",
        ""
    ]

    # --- Unidades ---
    unidades = {
        "temperatura": "°C", "humedad": "%", "presion": "hPa",
        "altitud": "m", "punto_rocio": "°C", "sensacion_termica": "°C",
        "densidad_aire": "kg/m³", "humedad_suelo": "%", "luz": "lux",
    }

    # --- Ejecución paralela para rapidez ---
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_feed, feed, start, end): feed for feed in FEEDS}

        for future in as_completed(futures):
            feed = futures[future]
            try:
                data = future.result()
                valores = parse_feed_data(data, interval_minutes=30)  # cada 30 minutos
                st = calc_stats(valores)

                if not st:
                    summary.append(f"• `{feed}`: sin datos 📭")
                    continue

                # Limpieza del nombre de feed
                key = (
                    feed.replace("estacion.", "")
                        .replace("estacion-dot-", "")
                        .replace("_", " ")
                )
                trend = trend_symbol(st["first"], st["last"])

                # Caso especial: control de riego
                if "rele" in key:
                    estado = "💧 Riego ACTIVADO" if st["last"] >= 1 else "💤 Riego APAGADO"
                    summary.append(f"• `{key}` → {estado}")
                    continue

                suf = unidades.get(key.strip(), "")
                summary.append(
                    f"• `{key}` → n={st['n']}, min={st['min']:.2f}{suf}, max={st['max']:.2f}{suf}, "
                    f"media={st['mean']:.2f}{suf}, σ={st['std']:.2f}, tendencia {trend}"
                )

                # Enviar gráfico
                img = make_plot(feed, valores)
                if img:
                    telegram_send_photo(img, caption=f"{key.title()} ({suf})")

            except Exception as e:
                summary.append(f"• `{feed}`: ⚠️ Error {e}")

    # --- Enviar resumen a Telegram ---
    telegram_send_text("\n".join(summary))
    print("✅ Reporte diario enviado con éxito.")

if __name__ == "__main__":
    main()
