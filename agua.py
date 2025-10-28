import os
import sys
import pytz
import requests
import statistics
import datetime as dt
import matplotlib.pyplot as plt
from pathlib import Path

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
    "estacion.punto_rocio",
    "estacion.sensacion_termica",
    "estacion.densidad_aire",
    "estacion.humedad_suelo",
    "estacion.luz",
    "estacion.rele_control"
]

if not all([TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, IO_USERNAME, IO_KEY]):
    sys.exit("❌ ERROR: Faltan variables de entorno necesarias.")


# =========================================================
# 🔧 FUNCIONES UTILITARIAS
# =========================================================
def safe_request(url, headers, params, retries=3):
    """Realiza una solicitud GET robusta con reintentos exponenciales."""
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=20)
            if r.status_code == 200:
                return r.json()
            else:
                print(f"⚠️ Error {r.status_code} en intento {attempt+1}")
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Excepción {e} en intento {attempt+1}")
        time.sleep(2 ** attempt)
    return []


def fetch_feed(feed_key, start, end):
    """Descarga todos los datos de un feed, usando paginación inteligente."""
    url = f"{BASE_URL}/{feed_key}/data"
    headers = {"X-AIO-Key": IO_KEY}
    all_data, page = [], 1
    limit = 1000

    while True:
        params = {"start_time": start, "end_time": end, "limit": limit, "page": page}
        data = safe_request(url, headers, params)
        if not data:
            break
        all_data.extend(data)
        if len(data) < limit:
            break
        page += 1
    return all_data


def parse_feed_data(data):
    """Convierte los datos crudos a [(timestamp, valor_float)]."""
    parsed = []
    for d in data:
        try:
            v = float(d["value"])
            t = dt.datetime.fromisoformat(d["created_at"].replace("Z", "+00:00")).astimezone(TZ)
            parsed.append((t, v))
        except (ValueError, KeyError):
            continue
    return sorted(parsed, key=lambda x: x[0])


def calc_stats(values):
    """Calcula estadísticas descriptivas básicas."""
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


def trend_symbol(first, last, threshold=0.1):
    """Determina la tendencia (↑ ↓ →) con un umbral."""
    if first is None or last is None:
        return "→"
    delta = last - first
    if abs(delta) < threshold:
        return "→"
    return "↑" if delta > 0 else "↓"


def make_plot(feed, values):
    """Genera un gráfico temporal con estilo limpio y lo guarda."""
    if not values:
        return None
    x, y = zip(*values)
    plt.figure(figsize=(6, 3))
    plt.plot(x, y, linewidth=1.5)
    plt.title(feed.replace("estacion.", "").replace("_", " ").title())
    plt.xlabel("Hora (Chile)")
    plt.ylabel("Valor")
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
# 🚀 PROCESO PRINCIPAL
# =========================================================
def main():
    now = dt.datetime.now(TZ)
    start = (now - dt.timedelta(days=1)).astimezone(pytz.UTC).isoformat()
    end = now.astimezone(pytz.UTC).isoformat()

    summary = [f"*📊 Reporte diario estación ({now:%Y-%m-%d})*", ""]

    unidades = {
        "temperatura": "°C",
        "humedad": "%",
        "presion": "hPa",
        "altitud": "m",
        "punto_rocio": "°C",
        "sensacion_termica": "°C",
        "densidad_aire": "kg/m³",
        "humedad_suelo": "%",
        "luz": "lux",
    }

    for feed in FEEDS:
        try:
            data = fetch_feed(feed, start, end)
            valores = parse_feed_data(data)
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
            summary.append(f"• `{feed}`: ⚠️ Error {str(e)}")

    telegram_send_text("\n".join(summary))
    print("✅ Reporte diario enviado con éxito.")


if __name__ == "__main__":
    import time
    main()
