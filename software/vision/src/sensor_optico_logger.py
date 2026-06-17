import csv
import json
import time
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

import requests


#CONFIGURACION

TIMEZONE = "Europe/Madrid"

ESP32_BASE_URL = "http://192.168.1.155"
ESP32_MEDIR_URL = f"{ESP32_BASE_URL}/medir"
ESP32_RESULTADO_URL = f"{ESP32_BASE_URL}/resultado"

BASE_DIR = Path("/home/vasi/Escritorio/vision/src")

SENSOR_LOG_DIR = BASE_DIR / "logs" / "sensor_optico"
SENSOR_LOG_DIR.mkdir(parents=True, exist_ok=True)

SUN_TIMES_CACHE = BASE_DIR / "sun_times_cache.json"

RESISTENCIA_SENSOR = "22k"

TIMEOUT_SENSOR = 330

INTERVALO_CONSULTA_RESULTADO = 10

CSV_FIELDNAMES = [
    "timestamp",
    "fecha",
    "hora",
    "resistencia_sensor",
    "media_adc_laser_off",
    "media_adc_laser_on",
    "diferencia_media_adc_off_menos_on",
    "laser_off_min_adc",
    "laser_off_max_adc",
    "laser_off_rango_adc",
    "laser_on_min_adc",
    "laser_on_max_adc",
    "laser_on_rango_adc",
]


#HORARIO SOLAR
def obtener_amanecer_anochecer_desde_cache(ahora):
    """Lee desde la caché local las horas de amanecer y anochecer del día actual."""
    if not SUN_TIMES_CACHE.exists():
        raise FileNotFoundError(f"No existe el archivo de cache solar: {SUN_TIMES_CACHE}")

    with open(SUN_TIMES_CACHE, "r", encoding="utf-8") as f:
        data = json.load(f)

    fecha_cache = data.get("date")
    fecha_hoy = ahora.strftime("%Y-%m-%d")

    if fecha_cache != fecha_hoy:
        raise ValueError(
            f"El cache solar no esta actualizado. "
            f"Fecha del cache: {fecha_cache}, fecha actual: {fecha_hoy}. "
            f"Ejecuta update_sun_times_cache.py antes de lanzar la medicion."
        )

    sunrise_str = data.get("sunrise")
    sunset_str = data.get("sunset")

    if not sunrise_str or not sunset_str:
        raise ValueError("El archivo sun_times_cache.json no contiene sunrise o sunset")

    sunrise = datetime.fromisoformat(sunrise_str)
    sunset = datetime.fromisoformat(sunset_str)

    return sunrise, sunset


def estamos_en_horas_solares(ahora):
    """Comprueba si la hora actual está dentro del horario solar permitido."""
    sunrise, sunset = obtener_amanecer_anochecer_desde_cache(ahora)
    return sunrise <= ahora <= sunset, sunrise, sunset


#SENSOR
def iniciar_medicion_sensor():
    """Envía a la ESP32 la orden para iniciar una nueva medición del sensor óptico."""
    r = requests.get(
        ESP32_MEDIR_URL,
        timeout=10,
        allow_redirects=False,
    )

    if r.status_code == 409:
        raise RuntimeError("La ESP32 ya tiene una medicion en curso.")

    if r.status_code not in (200, 303):
        r.raise_for_status()


def obtener_resultado_sensor():
    """Consulta a la ESP32 el estado o resultado de la medición del sensor óptico."""
    r = requests.get(ESP32_RESULTADO_URL, timeout=10)
    r.raise_for_status()
    return r.json()


def ejecutar_medicion_sensor():
    """Ejecuta la medición completa del sensor óptico y espera hasta obtener el resultado final."""
    iniciar_medicion_sensor()

    inicio = datetime.now(ZoneInfo(TIMEZONE))

    while True:
        datos_sensor = obtener_resultado_sensor()

        if not datos_sensor.get("midiendo", False):
            return datos_sensor

        ahora = datetime.now(ZoneInfo(TIMEZONE))
        segundos_esperando = (ahora - inicio).total_seconds()

        if segundos_esperando > TIMEOUT_SENSOR:
            raise TimeoutError("La medicion no termino dentro del tiempo esperado.")

        print("Medicion en curso. Esperando resultado...")
        time.sleep(INTERVALO_CONSULTA_RESULTADO)


#CSV DIARIO
def ruta_csv_sensor(ahora):
    """Genera la ruta del archivo CSV diario donde se guardará la medición del sensor."""
    nombre = f"sensor_optico_{ahora.strftime('%Y-%m-%d')}.csv"
    return SENSOR_LOG_DIR / nombre


def safe_float(value):
    """Convierte un valor a número decimal de forma segura."""
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except Exception:
        return None


def obtener_media(datos_sensor, bloque, clave_top_level):
    """Obtiene la media ADC de una fase de medición, priorizando el valor principal si existe."""
    valor_top_level = safe_float(datos_sensor.get(clave_top_level))

    if valor_top_level is not None:
        return valor_top_level

    return safe_float(bloque.get("media_adc"))


def calcular_diferencia_adc(datos_sensor, media_off, media_on):
    """Calcula la diferencia entre la media ADC con el láser apagado y encendido."""
    diferencia = safe_float(datos_sensor.get("diferencia_media_adc_off_menos_on"))

    if diferencia is not None:
        return diferencia

    if media_off is None or media_on is None:
        return ""

    return media_off - media_on


def valor_o_vacio(value):
    """Devuelve una cadena vacía cuando el valor no está disponible."""
    return "" if value is None else value


def crear_registro_csv(ahora, datos_sensor):
    """Construye el registro CSV con los datos principales de la medición óptica."""
    laser_off = datos_sensor.get("laser_off", {}) or {}
    laser_on = datos_sensor.get("laser_on", {}) or {}

    media_off = obtener_media(datos_sensor, laser_off, "media_adc_laser_off")
    media_on = obtener_media(datos_sensor, laser_on, "media_adc_laser_on")
    diferencia = calcular_diferencia_adc(datos_sensor, media_off, media_on)

    registro = {
        "timestamp": ahora.isoformat(timespec="seconds"),
        "fecha": ahora.strftime("%Y-%m-%d"),
        "hora": ahora.strftime("%H:%M:%S"),
        "resistencia_sensor": RESISTENCIA_SENSOR,
        "media_adc_laser_off": valor_o_vacio(media_off),
        "media_adc_laser_on": valor_o_vacio(media_on),
        "diferencia_media_adc_off_menos_on": valor_o_vacio(diferencia),
        "laser_off_min_adc": valor_o_vacio(laser_off.get("min_adc", "")),
        "laser_off_max_adc": valor_o_vacio(laser_off.get("max_adc", "")),
        "laser_off_rango_adc": valor_o_vacio(laser_off.get("rango_adc", "")),
        "laser_on_min_adc": valor_o_vacio(laser_on.get("min_adc", "")),
        "laser_on_max_adc": valor_o_vacio(laser_on.get("max_adc", "")),
        "laser_on_rango_adc": valor_o_vacio(laser_on.get("rango_adc", "")),
    }

    return registro


def leer_cabecera_csv(csv_path):
    """Lee la cabecera de un archivo CSV si existe y contiene datos."""
    if not csv_path.exists() or csv_path.stat().st_size == 0:
        return None

    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        return next(reader, None)


def normalizar_fila_existente(row):
    """Adapta una fila antigua del CSV al formato actual de columnas."""
    return {
        "timestamp": row.get("timestamp", ""),
        "fecha": row.get("fecha", ""),
        "hora": row.get("hora", ""),
        "resistencia_sensor": row.get("resistencia_sensor", "") or RESISTENCIA_SENSOR,
        "media_adc_laser_off": row.get("media_adc_laser_off", "") or row.get("laser_off_media_adc", ""),
        "media_adc_laser_on": row.get("media_adc_laser_on", "") or row.get("laser_on_media_adc", ""),
        "diferencia_media_adc_off_menos_on": row.get("diferencia_media_adc_off_menos_on", ""),
        "laser_off_min_adc": row.get("laser_off_min_adc", ""),
        "laser_off_max_adc": row.get("laser_off_max_adc", ""),
        "laser_off_rango_adc": row.get("laser_off_rango_adc", ""),
        "laser_on_min_adc": row.get("laser_on_min_adc", ""),
        "laser_on_max_adc": row.get("laser_on_max_adc", ""),
        "laser_on_rango_adc": row.get("laser_on_rango_adc", ""),
    }


def convertir_csv_existente_si_hace_falta(csv_path):
    """Convierte un CSV existente al formato actual si detecta una cabecera antigua."""
    cabecera = leer_cabecera_csv(csv_path)

    if cabecera is None or cabecera == CSV_FIELDNAMES:
        return

    fecha_backup = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y%m%d_%H%M%S")
    backup_path = csv_path.with_name(f"{csv_path.stem}_backup_{fecha_backup}{csv_path.suffix}")

    csv_path.rename(backup_path)

    with open(backup_path, "r", newline="", encoding="utf-8") as f:
        filas_antiguas = list(csv.DictReader(f))

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()

        for row in filas_antiguas:
            writer.writerow(normalizar_fila_existente(row))

    print("CSV existente convertido al formato limpio.")
    print(f"Copia del CSV anterior: {backup_path}")


def guardar_medicion_csv(ahora, datos_sensor):
    """Guarda la medición del sensor óptico en el CSV diario correspondiente."""
    csv_path = ruta_csv_sensor(ahora)
    registro = crear_registro_csv(ahora, datos_sensor)

    convertir_csv_existente_si_hace_falta(csv_path)

    archivo_existe = csv_path.exists() and csv_path.stat().st_size > 0

    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)

        if not archivo_existe:
            writer.writeheader()

        writer.writerow(registro)

    return csv_path, registro


#MAIN
def main():
    """Ejecuta el flujo completo de medición horaria del sensor óptico y guarda el resultado."""
    ahora = datetime.now(ZoneInfo(TIMEZONE))

    print("======================================")
    print("MEDICION HORARIA SENSOR OPTICO")
    print("======================================")
    print(f"Hora actual: {ahora.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Resistencia sensor: {RESISTENCIA_SENSOR}")
    print(f"Archivo cache solar: {SUN_TIMES_CACHE}")
    print(f"Carpeta logs sensor: {SENSOR_LOG_DIR}")

    try:
        dentro_horas_solares, sunrise, sunset = estamos_en_horas_solares(ahora)
    except Exception as e:
        print("ERROR: No se pudo leer amanecer/anochecer desde sun_times_cache.json.")
        print(e)
        return

    print(f"Amanecer:  {sunrise.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Anochecer: {sunset.strftime('%Y-%m-%d %H:%M:%S')}")

    if not dentro_horas_solares:
        print("Estado: fuera de horas solares. No se ejecuta medicion.")
        return

    print("Estado: dentro de horas solares.")
    print("Ejecutando medicion completa en ESP32...")
    print("Duracion esperada: 4 minutos.")

    try:
        datos_sensor = ejecutar_medicion_sensor()
    except Exception as e:
        print("ERROR: No se pudo ejecutar la medicion en la ESP32.")
        print(e)
        return

    try:
        csv_path, registro = guardar_medicion_csv(ahora, datos_sensor)
    except Exception as e:
        print("ERROR: No se pudo guardar la medicion en CSV.")
        print(e)
        return

    print("Medicion guardada correctamente.")
    print(f"Media laser OFF: {registro['media_adc_laser_off']}")
    print(f"Media laser ON: {registro['media_adc_laser_on']}")
    print(f"Diferencia ADC OFF - ON: {registro['diferencia_media_adc_off_menos_on']}")
    print(f"CSV diario: {csv_path}")


if __name__ == "__main__":
    main()

