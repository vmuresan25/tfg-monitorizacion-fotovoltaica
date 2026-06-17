import os
import subprocess
import re
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from pathlib import Path

#CONFIG
TIMEZONE = "Europe/Madrid"

BASE_DIR = Path("/home/vasi/Escritorio/vision/src")
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
RESULT_DIR = BASE_DIR / "result"
DEBUG_DIR = BASE_DIR / "debug"
SUN_CACHE_FILE = BASE_DIR / "sun_times_cache.json"
DAILY_SUMMARY_DIR = BASE_DIR / "daily_summary"

ESP32_URL = "http://192.168.1.145/capture"

PYTHON_PATH = "/home/vasi/Escritorio/vision/venv/bin/python"


#SOL
def get_sun_times_from_cache():
    """Lee desde la caché local las horas de amanecer y atardecer del día actual."""
    if not SUN_CACHE_FILE.exists():
        raise RuntimeError(
            f"No existe el archivo de caché de horas solares: {SUN_CACHE_FILE}. "
            "Ejecuta primero update_sun_times_cache.py."
        )

    with open(SUN_CACHE_FILE, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    tz = ZoneInfo(TIMEZONE)
    today = datetime.now(tz).strftime("%Y-%m-%d")

    cache_date = cache_data.get("date")

    if cache_date != today:
        raise RuntimeError(
            f"La caché de horas solares no es de hoy. "
            f"Fecha caché: {cache_date}, fecha actual: {today}"
        )

    sunrise = datetime.fromisoformat(cache_data["sunrise"])
    sunset = datetime.fromisoformat(cache_data["sunset"])

    return sunrise, sunset


#RESUMEN DIARIO
def ejecutar_resumen_diario_si_toca(now: datetime, sunset: datetime):
    """Ejecuta el resumen diario si ya ha pasado el tiempo mínimo después del atardecer."""
    resumen_desde = sunset + timedelta(minutes=20)

    if now < resumen_desde:
        return

    date_str = now.strftime("%Y-%m-%d")
    summary_file = DAILY_SUMMARY_DIR / f"{date_str}.csv"

    if summary_file.exists():
        return

    env = os.environ.copy()

    p = subprocess.run(
        [PYTHON_PATH, str(BASE_DIR / "daily_solar_summary.py")],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(BASE_DIR),
    )

    out = (p.stdout or "") + (p.stderr or "")

    if out.strip():
        print(out)

    if p.returncode != 0:
        print(f"ERROR ejecutando daily_solar_summary.py. Código: {p.returncode}")


#TELEGRAM
def send_telegram_message(text: str, silent: bool = True):
    """Envía un mensaje de texto al chat de Telegram configurado."""
    bot_token = os.environ["BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "disable_notification": silent,
        "parse_mode": "Markdown"
    }, timeout=15)


def send_telegram_photo(image_path: Path, silent: bool = True):
    """Envía una imagen al chat de Telegram configurado."""
    bot_token = os.environ["BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"

    with open(image_path, "rb") as f:
        requests.post(
            url,
            data={"chat_id": chat_id,
            "disable_notification": str(silent).lower()
            },
            files={"photo": f},
            timeout=20
        )


#RESUMEN
def generar_resumen_solar(texto: str):
    """Extrae los datos principales del diagnóstico solar y genera el mensaje resumen."""
    try:
        real_matches = re.findall(r"Potencia real:\s*([0-9.]+ kW)", texto)
        teorica_matches = re.findall(r"Potencia teórica:\s*([0-9.]+ kW)", texto)

        real = real_matches[-1] if real_matches else "N/A"
        teorica = teorica_matches[-1] if teorica_matches else "N/A"

        diff_matches = re.findall(r"Diferencia instantánea real - teórica:\s*([-0-9.]+ kW)", texto)
        diff = diff_matches[-1] if diff_matches else "N/A"

        estado_matches = re.findall(r"Estado final:\s*([a-zA-Z_]+)", texto)
        estado = estado_matches[-1] if estado_matches else "unknown"

        time_matches = re.findall(r"Hora real FusionSolar:\s*([0-9:\- ]+)", texto)
        aligned_time = time_matches[-1] if time_matches else "N/A"

        base = (
            f"Hora: {aligned_time}\n\n"
            f"Real: {real}\n"
            f"Teórica: {teorica}\n"
            f"Diferencia: {diff}"
        )

        if estado == "alert":
            mensaje = "*ALERTA DE PRODUCCIÓN*\n\n" + base
        elif estado == "warning":
            mensaje = "*WARNING DE PRODUCCIÓN*\n\n" + base
        else:
            mensaje = "*Producción normal*\n\n" + base

        return mensaje, estado

    except Exception:
        return "Error procesando datos", "error"


#CÁMARA
def capturar_imagen():
    """Captura una imagen desde la ESP32-CAM y la guarda en la carpeta de entrada."""
    try:
        r = requests.get(ESP32_URL, timeout=10, stream=True)
        r.raise_for_status()

        if "image" not in r.headers.get("Content-Type", ""):
            return None

        INPUT_DIR.mkdir(exist_ok=True)

        filename = INPUT_DIR / f"cam_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"

        with open(filename, "wb") as f:
            for chunk in r.iter_content(1024):
                f.write(chunk)

        return filename

    except Exception:
        return None


#DEBUG IMAGE
def obtener_ultima_imagen_debug():
    """Obtiene la última imagen de depuración generada por el sistema de visión."""
    try:
        if not DEBUG_DIR.exists():
            return None

        carpetas = [p for p in DEBUG_DIR.iterdir() if p.is_dir()]
        if not carpetas:
            return None

        ultima_carpeta = max(carpetas, key=lambda p: p.stat().st_mtime)

        imagenes = list(ultima_carpeta.glob("*.jpg"))
        if not imagenes:
            return None

        return max(imagenes, key=lambda p: p.stat().st_mtime)

    except Exception:
        return None


#VISIÓN
def ejecutar_vision():
    """Ejecuta los scripts de recorte del panel y detección de suciedad."""
    try:
        crop = subprocess.run(
            [PYTHON_PATH, str(BASE_DIR / "crop_panel_roboflow.py")],
            capture_output=True,
            text=True
        )

        salida_crop = (crop.stdout or "") + (crop.stderr or "")

        if "no devolvió predicciones" in salida_crop.lower():
            return False, "no_panel"

        if crop.returncode != 0:
            return False, "error_crop"

        detect = subprocess.run(
            [PYTHON_PATH, str(BASE_DIR / "detect_dirt_objects.py")],
            capture_output=True,
            text=True
        )

        if detect.returncode != 0:
            return False, "error_detect"

        return True, "ok"

    except Exception:
        return False, "error_general"


def obtener_ultima_imagen():
    """Obtiene la última imagen procesada guardada en la carpeta de resultados."""
    files = list(RESULT_DIR.glob("*.jpg"))
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


#MAIN
def main():
    """Ejecuta el flujo completo del monitor solar, incluyendo diagnóstico, cámara, visión y envío por Telegram."""
    now = datetime.now(ZoneInfo(TIMEZONE)).replace(second=0, microsecond=0)

    try:
        sunrise, sunset = get_sun_times_from_cache()
    except Exception as e:
        print(f"ERROR leyendo caché de horas solares: {e}")

        if os.environ.get("SKIP_TELEGRAM_SEND") != "1":
            send_telegram_message(
                "Error en monitor solar\n\n"
                "No se pudieron leer las horas solares desde la caché local.\n"
                "La ejecución se ha cancelado de forma controlada.",
                silent=False
            )

        return

    if now < sunrise:
        print(
            "Fuera de horas solares.\n\n"
            f"Hora actual: {now.strftime('%Y-%m-%d %H:%M')}\n"
            f"Amanecer: {sunrise.strftime('%H:%M')}\n"
            f"Atardecer: {sunset.strftime('%H:%M')}\n\n"
            "El monitor no se ejecuta antes del amanecer."
        )
        return

    if now > sunset:
        ejecutar_resumen_diario_si_toca(now, sunset)

        print(
            "Fuera de horas solares.\n\n"
            f"Hora actual: {now.strftime('%Y-%m-%d %H:%M')}\n"
            f"Amanecer: {sunrise.strftime('%H:%M')}\n"
            f"Atardecer: {sunset.strftime('%H:%M')}\n\n"
            "El monitor no se ejecuta después del atardecer."
        )
        return

    # MONITOR
    env = os.environ.copy()

    p = subprocess.run(
        [PYTHON_PATH, str(BASE_DIR / "logs_comparation.py")],
        capture_output=True,
        text=True,
        env=env,
    )

    out = (p.stdout or "") + (p.stderr or "")

    if p.returncode != 0 or "ERROR:" in out:
        mensaje_error_monitor = (
            "Error en monitor solar\n\n"
            "No se ha podido completar la comparación real/teórica.\n"
            "No se ejecuta la cámara para evitar un resultado confuso.\n\n"
            f"Detalle:\n{out[-1000:] if out else 'Sin salida del script.'}"
        )

        if os.environ.get("SKIP_TELEGRAM_SEND") == "1":
            print(mensaje_error_monitor)
        else:
            send_telegram_message(mensaje_error_monitor, silent=False)

        return

    mensaje, estado = generar_resumen_solar(out)

    if "N/A" in mensaje:
        mensaje_error_monitor = (
            "Sin datos válidos todavía. Se omite esta ejecución.\n\n"
            f"Salida parcial del monitor:\n{out[-1000:] if out else 'Sin salida del script.'}"
        )

        print(mensaje_error_monitor)
        return

    # VISIÓN
    img = capturar_imagen()
    mensaje_error = None

    if img:
        ok, tipo_error = ejecutar_vision()

        if ok:
            img_final = obtener_ultima_imagen()
        else:
            img_debug = obtener_ultima_imagen_debug()
            img_final = img_debug if img_debug else img

            if tipo_error == "no_panel":
                mensaje_error = (
                    "No se ha podido analizar la imagen\n\n"
                    "No se ha detectado ningún panel solar.\n"
                    "Posibles causas:\n"
                    "- Cámara mal orientada\n"
                    "- Mala iluminación\n"
                    "- Obstrucción en la imagen"
                )
            else:
                mensaje_error = (
                    "Error al procesar la imagen\n\n"
                    "El sistema de visión ha fallado.\n"
                    "Se recomienda revisar la cámara."
                )
    else:
        img_final = None
        mensaje_error = (
            "No se ha podido capturar la imagen\n\n"
            "No hay conexión con la cámara."
        )

    # TELEGRAM
    if os.environ.get("SKIP_TELEGRAM_SEND") == "1":
        print(mensaje)
        if mensaje_error:
            print(mensaje_error)
        return

    if estado == "alert":
        send_telegram_message(mensaje, silent=False)
    else:
        send_telegram_message(mensaje, silent=True)

    if mensaje_error:
        send_telegram_message(mensaje_error, silent=True)

    if img_final:
        send_telegram_photo(img_final, silent=True)


if __name__ == "__main__":
    main()
