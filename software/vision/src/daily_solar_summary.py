import csv
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


#CONFIG
TIMEZONE = "Europe/Madrid"

BASE_DIR = Path("/home/vasi/Escritorio/vision/src")
LOGS_DIR = BASE_DIR / "logs"
DAILY_SUMMARY_DIR = BASE_DIR / "daily_summary"
SUN_CACHE_FILE = BASE_DIR / "sun_times_cache.json"

OPTICAL_DIRS = [
    BASE_DIR / "logs" / "sensor_optico",
]

OPTICAL_CLEAN_RATIO = 0.072

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


#TELEGRAM
def send_telegram_message(text: str, silent: bool = True):
    """Envía un mensaje de texto al chat de Telegram configurado."""
    if not BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("Faltan BOT_TOKEN o TELEGRAM_CHAT_ID.")

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_notification": silent,
            "parse_mode": "Markdown",
        },
        timeout=15,
    )


#HELPERS
def safe_float(value):
    """Convierte un valor a número decimal de forma segura."""
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def load_sun_times():
    """Carga las horas de salida y puesta de sol desde la caché diaria."""
    if not SUN_CACHE_FILE.exists():
        raise RuntimeError(f"No existe la caché de horas solares: {SUN_CACHE_FILE}")

    import json

    with open(SUN_CACHE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    if data.get("date") != today:
        raise RuntimeError(
            f"La caché solar no es de hoy. Fecha caché: {data.get('date')}, hoy: {today}"
        )

    sunrise = datetime.fromisoformat(data["sunrise"])
    sunset = datetime.fromisoformat(data["sunset"])

    return today, sunrise, sunset


def read_history_rows(date_str: str):
    """Lee las filas del histórico energético correspondiente a una fecha."""
    history_file = LOGS_DIR / f"history_{date_str}.csv"

    if not history_file.exists():
        raise RuntimeError(f"No existe el histórico del día: {history_file}")

    with open(history_file, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


#ENERGIA
def estimate_energy_kwh(rows):
    """Calcula la energía real y teórica acumulada a partir de las muestras del histórico."""
    interval_hours = 20.0 / 60.0

    real_total = 0.0
    theoretical_total = 0.0
    valid_samples = 0

    for row in rows:
        real_kw = safe_float(row.get("real_power_kw"))
        theoretical_kw = safe_float(row.get("theoretical_power_kw"))

        if real_kw is None or theoretical_kw is None or theoretical_kw <= 0:
            continue

        real_total += real_kw * interval_hours
        theoretical_total += theoretical_kw * interval_hours
        valid_samples += 1

    return real_total, theoretical_total, valid_samples


#SENSOR ÓPTICO
def find_optical_file(date_str: str):
    """Busca el archivo CSV del sensor óptico correspondiente a una fecha."""
    possible_names = [
        f"sensor_optico_{date_str}.csv",
    ]

    for directory in OPTICAL_DIRS:
        for name in possible_names:
            file_path = directory / name
            if file_path.exists():
                return file_path

    return None


def read_optical_rows(date_str: str):
    """Lee las mediciones del sensor óptico correspondientes a una fecha."""
    optical_file = find_optical_file(date_str)

    if optical_file is None:
        return [], None

    with open(optical_file, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f)), optical_file


def extract_hour(row):
    """Extrae la hora de una medición del sensor óptico."""
    hour_text = str(row.get("hora", "")).strip()

    if hour_text:
        try:
            return int(hour_text.split(":")[0])
        except Exception:
            pass

    timestamp = str(row.get("timestamp", "")).strip()

    if timestamp:
        try:
            return datetime.fromisoformat(timestamp).hour
        except Exception:
            pass

    return None


def extract_optical_values(row):
    """Extrae los valores medios del sensor óptico con el láser apagado y encendido."""
    off = safe_float(row.get("media_adc_laser_off"))
    on = safe_float(row.get("media_adc_laser_on"))

    if off is not None and on is not None:
        return off, on

    return None, None


def calculate_optical_summary(date_str: str):
    """Calcula el resumen diario del sensor óptico y estima el nivel de suciedad."""
    rows, optical_file = read_optical_rows(date_str)

    if not rows:
        return {
            "optical_available": False,
            "optical_status": "unknown",
            "optical_ratio": "",
            "optical_index_pct": "",
            "optical_valid_samples": 0,
            "optical_message": "sin datos opticos",
            "optical_file": "",
        }

    ratios = []

    for row in rows:
        hour = extract_hour(row)

        if hour is None:
            continue

        if hour < 10 or hour > 16:
            continue

        off, on = extract_optical_values(row)

        if off is None or on is None or off <= 0:
            continue

        ratio = (off - on) / off

        if ratio <= 0:
            continue

        ratios.append(ratio)

    if not ratios:
        return {
            "optical_available": False,
            "optical_status": "unknown",
            "optical_ratio": "",
            "optical_index_pct": "",
            "optical_valid_samples": 0,
            "optical_message": "sin muestras opticas validas",
            "optical_file": str(optical_file),
        }

    optical_ratio = sum(ratios) / len(ratios)
    optical_index_pct = optical_ratio / OPTICAL_CLEAN_RATIO * 100.0

    if optical_index_pct <= 120:
        status = "clean"
        message = "limpieza no necesaria"
    elif optical_index_pct <= 140:
        status = "slightly_dirty"
        message = "limpieza recomendable"
    else:
        status = "dirty"
        message = "limpieza muy recomendable"

    return {
        "optical_available": True,
        "optical_status": status,
        "optical_ratio": optical_ratio,
        "optical_index_pct": optical_index_pct,
        "optical_valid_samples": len(ratios),
        "optical_message": message,
    }


def calculate_cleaning_status(summary):
    """Determina el diagnóstico de limpieza combinando rendimiento energético y sensor óptico."""
    performance = summary["daily_performance_pct"]
    optical_status = summary["optical_status"]
    alert_count = summary["alert_count"]

    if optical_status == "unknown":
        if performance >= 90 and alert_count == 0:
            return "Diagnóstico: Producción correcta. Sensor óptico sin datos."
        elif performance >= 80:
            return "Diagnóstico: Producción algo baja. Sensor óptico sin datos. Revisar mañana."
        else:
            return "Diagnóstico: Producción baja. Sensor óptico sin datos. No se puede confirmar suciedad."

    if performance >= 90 and optical_status == "clean":
        return "Limpieza: No necesaria"

    if performance >= 90 and optical_status == "slightly_dirty":
        return "Limpieza: Revisar mañana"

    if performance >= 90 and optical_status == "dirty":
        return "Limpieza: Recomendada"

    if 80 <= performance < 90:
        if optical_status == "clean":
            return "Limpieza: No urgente"
        if optical_status == "slightly_dirty":
            return "Limpieza: Recomendada mañana"
        if optical_status == "dirty":
            return "Limpieza: Muy recomendable"

    if performance < 80:
        if optical_status == "clean":
            return "Diagnóstico: Producción baja, pero sensor óptico limpio. Revisar clima, sombras, datos teóricos o sistema eléctrico."
        if optical_status == "slightly_dirty":
            return "Diagnóstico: Producción baja y ligera suciedad detectada. Limpieza recomendable."
        if optical_status == "dirty":
            return "Diagnóstico: Producción baja y suciedad detectada. Limpieza muy recomendable."

    return "Limpieza: Revisar mañana"


#RESUMEN
def calculate_summary(date_str: str, sunrise: datetime, sunset: datetime):
    """Genera el resumen diario completo del sistema fotovoltaico."""
    rows = read_history_rows(date_str)

    real_total_kwh, theoretical_total_kwh, valid_samples = estimate_energy_kwh(rows)

    if valid_samples == 0 or theoretical_total_kwh <= 0:
        raise RuntimeError("No hay muestras válidas suficientes para generar el resumen diario.")

    difference_kwh = real_total_kwh - theoretical_total_kwh
    daily_ratio = real_total_kwh / theoretical_total_kwh
    daily_performance_pct = daily_ratio * 100.0

    normal_count = 0
    warning_count = 0
    alert_count = 0

    for row in rows:
        persistent_alert = str(row.get("persistent_alert", "")).strip().lower() in ("true", "1", "yes", "si", "sí")
        persistent_warning = str(row.get("persistent_warning", "")).strip().lower() in ("true", "1", "yes", "si", "sí")
        instant_status = str(row.get("instant_status", "")).strip().lower()

        if persistent_alert:
            final_status = "alert"
        elif persistent_warning:
            final_status = "warning"
        else:
            final_status = instant_status

        if final_status == "normal":
            normal_count += 1
        elif final_status == "warning":
            warning_count += 1
        elif final_status == "alert":
            alert_count += 1

    if daily_performance_pct >= 90:
        day_status = "normal"
    elif daily_performance_pct >= 80:
        day_status = "warning"
    else:
        day_status = "alert"

    optical = calculate_optical_summary(date_str)

    summary = {
        "date": date_str,
        "generated_at": datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d %H:%M:%S"),
        "sunrise": sunrise.strftime("%H:%M"),
        "sunset": sunset.strftime("%H:%M"),
        "real_total_kwh": real_total_kwh,
        "theoretical_total_kwh": theoretical_total_kwh,
        "difference_kwh": difference_kwh,
        "daily_ratio": daily_ratio,
        "daily_performance_pct": daily_performance_pct,
        "valid_samples": valid_samples,
        "normal_count": normal_count,
        "warning_count": warning_count,
        "alert_count": alert_count,
        "day_status": day_status,
        "telegram_message_sent": False,
    }

    summary.update(optical)
    summary["cleaning_status"] = calculate_cleaning_status(summary)

    return summary


def build_telegram_message(summary):
    """Construye el mensaje de resumen diario que se enviará por Telegram."""
    status = summary["day_status"].upper()

    msg = (
        "*RESUMEN SOLAR DIARIO*\n\n"
        f"Fecha: {summary['date']}\n"
        f"Rendimiento: {summary['daily_performance_pct']:.1f} %\n"
        f"Estado: {status}\n"
        f"Incidencias: {summary['warning_count']} warnings, {summary['alert_count']} alertas\n\n"
        f"{summary['cleaning_status']}"
    )

    return msg


def save_summary_csv(summary, summary_file: Path):
    """Guarda el resumen diario en un archivo CSV."""
    file_exists = summary_file.exists()

    fieldnames = [
        "date",
        "generated_at",
        "sunrise",
        "sunset",
        "real_total_kwh",
        "theoretical_total_kwh",
        "difference_kwh",
        "daily_ratio",
        "daily_performance_pct",
        "valid_samples",
        "normal_count",
        "warning_count",
        "alert_count",
        "day_status",
        "optical_available",
        "optical_status",
        "optical_ratio",
        "optical_index_pct",
        "optical_valid_samples",
        "optical_message",
        "cleaning_status",
        "telegram_message_sent",
    ]

    with open(summary_file, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        if not file_exists:
            writer.writeheader()

        writer.writerow({key: summary.get(key, "") for key in fieldnames})


#MAIN
def main():
    """Ejecuta el proceso completo de generación, envío y guardado del resumen diario."""
    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    date_str, sunrise, sunset = load_sun_times()

    summary_allowed_time = sunset + timedelta(minutes=20)

    if now < summary_allowed_time:
        print(
            f"Todavía no toca resumen diario. "
            f"Sunset: {sunset.strftime('%H:%M')}, "
            f"resumen permitido desde: {summary_allowed_time.strftime('%H:%M')}"
        )
        return

    DAILY_SUMMARY_DIR.mkdir(parents=True, exist_ok=True)

    summary_file = DAILY_SUMMARY_DIR / f"{date_str}.csv"

    summary = calculate_summary(date_str, sunrise, sunset)

    if summary_file.exists():
        with open(summary_file, "r", newline="", encoding="utf-8") as f:
            existing_rows = list(csv.DictReader(f))

        for row in existing_rows:
            if row.get("date") == date_str:
                print(f"El resumen diario ya existe para la fecha: {date_str}")
                return

    message = build_telegram_message(summary)

    if os.environ.get("SKIP_TELEGRAM_SEND") == "1":
        print(message)
    else:
        send_telegram_message(message, silent=False)
        summary["telegram_message_sent"] = True

    save_summary_csv(summary, summary_file)

    print(f"Resumen diario guardado en: {summary_file}")


if __name__ == "__main__":
    main()
