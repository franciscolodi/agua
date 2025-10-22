import os
import sys
import pytz
import math
import requests
import statistics
import datetime as dt
import matplotlib.pyplot as plt

# === Zona horaria ===
TZ = pytz.timezone("America/Santiago")

# === Credenciales (desde variables de entorno) ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IO_USERNAME = os.getenv("IO_USERNAME")
IO_KEY = os.getenv("IO_KEY")

# === Feeds a consultar ===
FEEDS = os.getenv(
    "FEEDS",
    "weather.temperature_c,weather.humidity_pct,weather.pressure_hpa,"
    "weather.altitude_m,weather.air_density_kgm3,weather.relay_control"
)

# === Validación de credenciales ===
if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, IO_USERNAME, IO_KEY]):
    print("❌ ERROR: faltan variables de entorno requeridas.")
    print("Debes definir: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, IO_USERNAME, IO_KEY")
    sys.exit(1)

BASE_URL = f"https://io.adafruit.com/api/v2/{IO_USERNAME}/feeds"


# === Funciones auxiliares ===
def fetch_feed(feed_key, start, end):
    """Descarga datos del feed en el rango de tiempo indicado."""
    # Asegurar slug válido para la API (replace . _ por - y a minúsculas)
    feed_slug = feed_key.replace(".", "-").replace("_", "-").lower()
    url = f"{BASE_URL}/{feed_slug}/data"
    headers = {"X-AIO-Key": IO_KEY}
    params = {"start_time": start, "end_time": end, "include": "value,created_at"}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code != 200:
        raise Exception(f"Error {r.status_code}: {r.text}")
    return r.json()


def parse_feed_data(data):
    """Convierte los datos del feed a una lista [(timestamp, valor)]."""
    out = []
    for d in data:
        try:
            v = float(d["value"])
            t = dt.datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(TZ)
            out.append((t, v))
        except:
            continue
    return sorted(out, key=lambda x: x[0])


def stats(values):
    """Calcula estadísticas básicas de una serie de datos."""
    if not values:
        return None
    arr = [v for _, v in values]
    return {
        "n": len(arr),
        "min": min(arr),
        "max": max(arr),
        "mean": statistics.fmean(arr),
        "median": statistics.median(arr),
        "std": statistics.pstdev(arr) if len(arr) > 1 else 0.0,
        "first": arr[0],
        "last": arr[-1],
    }


def trend_symbol(first, last):
    """Devuelve una flecha que indica la tendencia (↑ ↓ →)."""
    if first is None or last is None:
        return "→"
    delta = last - first
    if abs(delta) < 0.05:
        return "→"
    return "↑" if delta > 0 else "↓"


def telegram_send_text(text):
    """Envía un mensaje de texto a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    r = requests.post(url, json=payload, timeout=30)
    if r.status_code != 200:
        print(f"⚠️ Error enviando texto a Telegram: {r.text}")


def telegram_send_photo(image_path, caption=None):
    """Envía una imagen a Telegram con una descripción opcional."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(image_path, "rb") as img:
        files = {"photo": img}
        data = {"chat_id": TELEGRAM_CHAT_ID, "caption": caption or ""}
        r = requests.post(url, files=files, data=data, timeout=30)
        if r.status_code != 200:
            print(f"⚠️ Error enviando imagen a Telegram: {r.text}")


def make_plot(feed, values):
    """Genera un gráfico de un feed y lo guarda en /tmp."""
    if not values:
        return None
    x = [t for t, _ in values]
    y = [v for _, v in values]

    plt.figure(figsize=(6, 3))
    plt.plot(x, y, marker="o", linewidth=1.8)
    plt.title(feed.replace("weather.", "").replace("_", " ").title())
    plt.xlabel("Hora (Chile)")
    plt.ylabel("Valor")
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()

    filename = f"/tmp/{feed}.png"
    plt.savefig(filename)
    plt.close()
    return filename


# === MAIN ===
def main():
    now = dt.datetime.now(TZ)
    start = (now - dt.timedelta(days=1)).astimezone(pytz.UTC).isoformat()
    end = now.astimezone(pytz.UTC).isoformat()

    feeds = [f.strip() for f in FEEDS.split(",") if f.strip()]
    summary_lines = [f"*🌦 Reporte diario Adafruit IO ({now.strftime('%Y-%m-%d')})*", ""]

    for feed in feeds:
        try:
            raw = fetch_feed(feed, start, end)
            vals = parse_feed_data(raw)
            st = stats(vals)
            if not st:
                summary_lines.append(f"• `{feed}`: sin datos")
                continue

            unit = ""
            if "temperature" in feed: unit = "°C"
            elif "humidity" in feed: unit = "%"
            elif "pressure" in feed: unit = "hPa"
            elif "altitude" in feed: unit = "m"
            elif "density" in feed: unit = "kg/m³"

            if "relay" in feed:
                estado = "ON 🔌" if st["last"] >= 1 else "OFF ⚡"
                summary_lines.append(f"• `{feed}` → {estado}")
                continue

            trend = trend_symbol(st["first"], st["last"])
            summary_lines.append(
                f"• `{feed}` → n={st['n']}, min={st['min']:.2f}{unit}, max={st['max']:.2f}{unit}, "
                f"media={st['mean']:.2f}{unit}, σ={st['std']:.2f}{unit}, tendencia {trend}"
            )

            # Generar y enviar gráfico
            graph = make_plot(feed, vals)
            if graph:
                telegram_send_photo(graph, caption=f"{feed.replace('weather.', '').title()} ({unit})")

        except Exception as e:
            summary_lines.append(f"• `{feed}`: error {e}")

    # Enviar resumen de texto
    telegram_send_text("\n".join(summary_lines))
    print("✅ Reporte + gráficos enviados a Telegram.")


if __name__ == "__main__":
    main()
