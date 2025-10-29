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
    "estacion.temperatura", "estacion.humedad", "estacion.presion",
    "estacion.altitud", "estacion.punto_rocio", "estacion.sensacion_termica",
    "estacion.densidad_aire", "estacion.humedad_suelo", "estacion.luz",
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

def make_plot(feed, values):
    if not values: return None
    x, y = zip(*values)
    plt.figure(figsize=(5, 2.5), dpi=100)
    plt.plot(x, y, linewidth=1.3)
    plt.title(feed.replace("estacion.", "").title())
    plt.grid(alpha=0.3)
    plt.tight_layout()
    path = Path(f"/tmp/{feed}.png")
    plt.savefig(path)
    plt.close()
    return str(path)

def telegram_send_text(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})

def telegram_send_photo(path, caption=""):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    with open(path, "rb") as f:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption}, files={"photo": f})

# =========================================================
# 🚀 PROCESO PRINCIPAL (PARALELO)
# =========================================================
def main():
    now = dt.datetime.now(TZ)
    start = (now - dt.timedelta(days=1)).astimezone(pytz.UTC).isoformat()
    end = now.astimezone(pytz.UTC).isoformat()

    summary = [f"*📊 Reporte diario estación ({now:%Y-%m-%d})*", ""]

    unidades = {
        "temperatura": "°C", "humedad": "%", "presion": "hPa",
        "altitud": "m", "punto_rocio": "°C", "sensacion_termica": "°C",
        "densidad_aire": "kg/m³", "humedad_suelo": "%", "luz": "lux",
    }

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_feed, feed, start, end): feed for feed in FEEDS}
        for future in as_completed(futures):
            feed = futures[future]
            try:
                data = future.result()
                valores = parse_feed_data(data, interval_minutes=30)
                st = calc_stats(valores)

                if not st:
                    summary.append(f"• `{feed}`: sin datos 📭")
                    continue

                key = feed.replace("estacion.", "")
                trend = trend_symbol(st["first"], st["last"])

                if "rele" in key:
                    estado = "💧 Riego ACTIVADO" if st["last"] >= 1 else "💤 Riego APAGADO"
                    summary.append(f"• `{key}` → {estado}")
                    continue

                suf = unidades.get(key, "")
                summary.append(
                    f"• `{key}` → n={st['n']}, min={st['min']:.2f}{suf}, max={st['max']:.2f}{suf}, "
                    f"media={st['mean']:.2f}{suf}, σ={st['std']:.2f}, tendencia {trend}"
                )

                img = make_plot(feed, valores)
                if img:
                    telegram_send_photo(img, caption=f"{key.title()} ({suf})")

            except Exception as e:
                summary.append(f"• `{feed}`: ⚠️ Error {e}")

    telegram_send_text("\n".join(summary))
    print("✅ Reporte diario enviado con éxito.")


if __name__ == "__main__":
    main()
