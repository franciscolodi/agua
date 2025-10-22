import os
import sys
import pytz
import requests
import statistics
import datetime as dt
import matplotlib.pyplot as plt

# === Zona horaria ===
TZ = pytz.timezone("America/Santiago")

# === Credenciales desde GitHub Secrets ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
IO_USERNAME = os.getenv("IO_USERNAME")
IO_KEY = os.getenv("IO_KEY")

# === Feeds correctos según tu cuenta Adafruit ===
FEEDS = (
    "weather-dot-temperature-c,"
    "weather-dot-dew-point-c,"
    "weather-dot-pressure-hpa,"
    "weather-dot-humidity-pct,"
    "weather-dot-heat-index-c,"
    "weather-dot-air-density-kgm3,"
    "weather-dot-altitude-m,"
    "weather-dot-relay-control"
)

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, IO_USERNAME, IO_KEY]):
    print("❌ ERROR: faltan variables de entorno requeridas.")
    sys.exit(1)

BASE_URL = f"https://io.adafruit.com/api/v2/{IO_USERNAME}/feeds"


# === Funciones ===
def fetch_feed(feed_key, start, end):
    """Descarga datos del feed."""
    url = f"{BASE_URL}/{feed_key}/data"
    headers = {"X-AIO-Key": IO_KEY}
    params = {"start_time": start, "end_time": end}
    r = requests.get(url, headers=headers, params=params, timeout=30)
    if r.status_code != 200:
        raise Exception(f"Error {r.status_code}: {r.text}")
    return r.json()


def parse_feed_data(data):
    """Convierte datos crudos en lista de (timestamp, valor)."""
    out = []
    for d in data:
        try:
            v = float(d["value"])
            t = dt.datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(TZ)
            out.append((t, v))
        except Exception:
            continue
    return sorted(out, key=lambda x: x[0])


def stats(values):
    """Estadísticas básicas."""
    if not values:
        return None
    arr = [v for _, v in values]
    return {
        "n": len(arr),
        "min": min(arr),
        "max": max(arr),
        "mean": statistics.fmean(arr),
        "std": statistics.pstdev(arr) if len(arr) > 1 else 0,
        "first": arr[0],
        "last": arr[-1],
    }


def trend_symbol(first, last):
    if first is None or last is None:
        return "→"
    delta = last - first
    if abs(delta) < 0.05:
        return "→"
    return "↑" if delta > 0 else "↓"


def make_plot(feed, values):
    """Genera gráfico simple."""
    if not values:
        return None
    x = [t for t, _ in values]
    y = [v for _, v in values]

    plt.figure(figsize=(6, 3))
    plt.plot(x, y, linewidth=1.5)
    plt.title(feed.replace("weather-dot-", "").replace("-", " ").title())
    plt.xlabel("Hora (Chile)")
    plt.ylabel("Valor")
    plt.grid(True)
    plt.tight_layout()

    path = f"/tmp/{feed}.png"
    plt.savefig(path)
    plt.close()
    return path


def telegram_send_text(msg):
    """Envia texto a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})


def telegram_send_photo(path, caption=""):
    """Envia imagen a Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"photo": f})


# === MAIN ===
def main():
    now = dt.datetime.now(TZ)
    start = (now - dt.timedelta(days=1)).astimezone(pytz.UTC).isoformat()
    end = now.astimezone(pytz.UTC).isoformat()

    summary = [f"*🌦 Reporte diario Adafruit IO ({now.strftime('%Y-%m-%d')})*", ""]

    for feed in FEEDS.split(","):
        try:
            raw = fetch_feed(feed, start, end)
            vals = parse_feed_data(raw)
            st = stats(vals)

            if not st:
                summary.append(f"• `{feed}`: sin datos")
                continue

            trend = trend_symbol(st["first"], st["last"])

            if "relay" in feed:
                estado = "ON 🔌" if st["last"] >= 1 else "OFF ⚡"
                summary.append(f"• `{feed}` → {estado}")
                continue

            summary.append(
                f"• `{feed}` → n={st['n']}, min={st['min']:.2f}, max={st['max']:.2f}, "
                f"media={st['mean']:.2f}, σ={st['std']:.2f}, tendencia {trend}"
            )

            img = make_plot(feed, vals)
            if img:
                telegram_send_photo(img, caption=feed)

        except Exception as e:
            summary.append(f"• `{feed}`: error {e}")

    telegram_send_text("\n".join(summary))
    print("✅ Reporte enviado a Telegram con gráficos.")


if __name__ == "__main__":
    main()
