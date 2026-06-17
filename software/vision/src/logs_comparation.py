import csv
import os
import re
import time
import json
from datetime import datetime, timedelta, timezone

import requests
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


# CONFIGURACIÓN GENERAL
URL_LOGIN = "https://eu5.fusionsolar.huawei.com"
URL_DATA = "https://uni003eu5.fusionsolar.huawei.com/rest/pvms/web/station/v3/overview/energy-balance"
STATION_DN = "NE=137424663"

LATITUDE = 36.8039
LONGITUDE = -2.617
TILT = 30
AZIMUTH = -30
INSTALLED_POWER_KWP = 5.0

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_PARAMS = {
    "latitude": LATITUDE,
    "longitude": LONGITUDE,
    "minutely_15": "global_tilted_irradiance_instant,temperature_2m",
    "tilt": TILT,
    "azimuth": AZIMUTH,
    "timezone": "Europe/Madrid",
    "forecast_minutely_15": 96,
    "past_minutely_15": 8
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_FILE = os.path.join(BASE_DIR, ".fusionsolar_session.json")
OPEN_METEO_CACHE_FILE = os.path.join(BASE_DIR, "open_meteo_cache.json")
HISTORY_DIR = os.path.join(BASE_DIR, "logs")
MAX_HISTORY_DAYS = 30

OPEN_METEO_MAX_RETRIES = 2
OPEN_METEO_TIMEOUT_SECONDS = 7
OPEN_METEO_RETRY_SLEEP_SECONDS = 3
OPEN_METEO_CACHE_MAX_AGE_MINUTES = 360

MIN_GTI_FOR_ALERTS = 400.0
MAX_FALLBACK_STEPS = 4
WINDOW_SIZE_SAMPLES = 2

PERSISTENT_ALERT_THRESHOLD = 4

ALGORITHM_VERSION = "v15_openmeteo_cache_used_2_retries"



# CREDENCIALES
USERNAME = os.environ.get("FUSIONSOLAR_USERNAME", "").strip()
PASSWORD = os.environ.get("FUSIONSOLAR_PASSWORD", "").strip()

if not USERNAME or not PASSWORD:
    raise RuntimeError("Faltan FUSIONSOLAR_USERNAME o FUSIONSOLAR_PASSWORD.")


# TIEMPO
def get_local_tz():
    """Obtiene la zona horaria local configurada para el sistema."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo("Europe/Madrid")
        except Exception:
            pass
    return timezone(timedelta(hours=1), name="CET_FALLBACK")


LOCAL_TZ = get_local_tz()


def now_local():
    """Devuelve la fecha y hora actual en la zona horaria local."""
    return datetime.now(LOCAL_TZ)


def get_local_timezone_offset_minutes():
    """Calcula el desfase horario local respecto a UTC en minutos."""
    offset = now_local().utcoffset()
    return int(offset.total_seconds() // 60) if offset else 0


def get_local_timezone_hours_string():
    """Devuelve el desfase horario local en formato de horas."""
    return f"{get_local_timezone_offset_minutes() / 60.0:.1f}"


def get_local_midnight_string():
    """Devuelve la fecha actual con la hora fijada a medianoche."""
    return now_local().strftime("%Y-%m-%d 00:00:00")


def floor_to_15_minutes(dt):
    """Redondea una fecha y hora al bloque inferior de 15 minutos."""
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def is_quarter_hour(dt):
    """Comprueba si una fecha y hora está alineada con un cuarto de hora."""
    return dt.minute in (0, 15, 30, 45)


def parse_fusionsolar_datetime(dt_str):
    """Convierte una fecha recibida de FusionSolar a un objeto datetime."""
    raw = str(dt_str).strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::\d{2})?", raw)
    if not m:
        raise ValueError(f"Formato de fecha/hora FusionSolar no reconocido: {dt_str!r}")
    return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")


# CSV
CSV_FIELDNAMES = [
    "execution_time", "real_time", "requested_time_key", "theoretical_time",
    "open_meteo_cache_used",
    "used_fallback", "fallback_steps_used", "real_power_kw", "gti_w_m2",
    "temperature_c", "theoretical_power_kw", "instant_ratio", "instant_diff_kw",
    "instant_status", "window_real_avg_kw", "window_theoretical_avg_kw",
    "window_gti_avg_w_m2", "window_ratio", "window_diff_kw", "window_status",
    "consecutive_window_alerts", "consecutive_window_warning_or_alert",
    "persistent_alert", "persistent_warning", "algorithm_version"
]


def get_today_history_file():
    """Devuelve la ruta del archivo histórico CSV correspondiente al día actual."""
    return os.path.join(HISTORY_DIR, f"history_{now_local().strftime('%Y-%m-%d')}.csv")


def cleanup_old_history_files():
    """Elimina archivos históricos antiguos para mantener solo los días configurados."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    files = [f for f in os.listdir(HISTORY_DIR) if f.startswith("history_") and f.endswith(".csv")]
    files.sort()

    if len(files) > MAX_HISTORY_DAYS:
        for f in files[:-MAX_HISTORY_DAYS]:
            try:
                os.remove(os.path.join(HISTORY_DIR, f))
            except Exception as e:
                print(f"No se pudo borrar {f}: {e}")


def normalize_row(row):
    """Normaliza una fila del histórico para que contenga todos los campos esperados."""
    return {field: row.get(field, "") for field in CSV_FIELDNAMES}


def ensure_history_file():
    """Crea el archivo histórico diario si todavía no existe."""
    os.makedirs(HISTORY_DIR, exist_ok=True)
    today_file = get_today_history_file()

    if not os.path.exists(today_file):
        with open(today_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()


def read_history_rows():
    """Lee las filas del histórico diario actual."""
    today_file = get_today_history_file()
    if not os.path.exists(today_file):
        return []

    with open(today_file, "r", newline="", encoding="utf-8") as f:
        return [normalize_row(row) for row in csv.DictReader(f)]


def upsert_history_row(
    execution_time, real_time, requested_time_key, theoretical_time,
    open_meteo_cache_used,
    used_fallback, fallback_steps_used, real_power_kw, gti, temperature,
    theoretical_power_kw, instant_ratio, instant_diff_kw, instant_status,
    window_real_avg_kw, window_theoretical_avg_kw, window_gti_avg_w_m2,
    window_ratio, window_diff_kw, window_status, consecutive_window_alerts,
    consecutive_window_warning_or_alert, persistent_alert, persistent_warning,
    algorithm_version=ALGORITHM_VERSION
):
    """Inserta o actualiza una fila del histórico con los datos del diagnóstico solar."""
    cleanup_old_history_files()
    ensure_history_file()
    today_file = get_today_history_file()

    new_row = normalize_row({
        "execution_time": execution_time,
        "real_time": real_time,
        "requested_time_key": requested_time_key,
        "theoretical_time": theoretical_time,
        "open_meteo_cache_used": str(open_meteo_cache_used),
        "used_fallback": str(used_fallback),
        "fallback_steps_used": str(fallback_steps_used),
        "real_power_kw": f"{real_power_kw:.6f}" if real_power_kw is not None else "",
        "gti_w_m2": f"{gti:.6f}" if gti is not None else "",
        "temperature_c": f"{temperature:.6f}" if temperature is not None else "",
        "theoretical_power_kw": f"{theoretical_power_kw:.6f}" if theoretical_power_kw is not None else "",
        "instant_ratio": f"{instant_ratio:.6f}" if instant_ratio is not None else "",
        "instant_diff_kw": f"{instant_diff_kw:.6f}" if instant_diff_kw is not None else "",
        "instant_status": instant_status,
        "window_real_avg_kw": f"{window_real_avg_kw:.6f}" if window_real_avg_kw is not None else "",
        "window_theoretical_avg_kw": f"{window_theoretical_avg_kw:.6f}" if window_theoretical_avg_kw is not None else "",
        "window_gti_avg_w_m2": f"{window_gti_avg_w_m2:.6f}" if window_gti_avg_w_m2 is not None else "",
        "window_ratio": f"{window_ratio:.6f}" if window_ratio is not None else "",
        "window_diff_kw": f"{window_diff_kw:.6f}" if window_diff_kw is not None else "",
        "window_status": window_status,
        "consecutive_window_alerts": str(consecutive_window_alerts),
        "consecutive_window_warning_or_alert": str(consecutive_window_warning_or_alert),
        "persistent_alert": str(persistent_alert),
        "persistent_warning": str(persistent_warning),
        "algorithm_version": algorithm_version
    })

    rows = read_history_rows()
    updated = False
    execution_minute = execution_time[:16]

    for i, row in enumerate(rows):
        if row.get("execution_time", "")[:16] == execution_minute:
            rows[i] = new_row
            updated = True
            break

    if not updated:
        rows.append(new_row)

    rows.sort(key=lambda r: r.get("execution_time", ""))

    with open(today_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return "updated" if updated else "inserted"


def upsert_error_history_row(error_message):
    """Guarda en el histórico una fila de error cuando falla el monitor solar."""
    cleanup_old_history_files()
    ensure_history_file()

    execution_time = now_local().strftime("%Y-%m-%d %H:%M:%S")
    execution_minute = execution_time[:16]
    today_file = get_today_history_file()

    error_short = str(error_message).replace("\n", " ")[:120]

    new_row = normalize_row({
        "execution_time": execution_time,
        "real_time": execution_minute,
        "requested_time_key": "",
        "theoretical_time": "",
        "open_meteo_cache_used": "",
        "used_fallback": "False",
        "fallback_steps_used": "0",
        "real_power_kw": "",
        "gti_w_m2": "",
        "temperature_c": "",
        "theoretical_power_kw": "",
        "instant_ratio": "",
        "instant_diff_kw": "",
        "instant_status": "error_monitor",
        "window_real_avg_kw": "",
        "window_theoretical_avg_kw": "",
        "window_gti_avg_w_m2": "",
        "window_ratio": "",
        "window_diff_kw": "",
        "window_status": "error_monitor",
        "consecutive_window_alerts": "0",
        "consecutive_window_warning_or_alert": "0",
        "persistent_alert": "False",
        "persistent_warning": "False",
        "algorithm_version": f"{ALGORITHM_VERSION}_ERROR_{error_short}"
    })

    rows = read_history_rows()
    updated = False

    for i, row in enumerate(rows):
        if row.get("execution_time", "")[:16] == execution_minute:
            rows[i] = new_row
            updated = True
            break

    if not updated:
        rows.append(new_row)

    rows.sort(key=lambda r: r.get("execution_time", ""))

    with open(today_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    return "updated" if updated else "inserted"


# FUSIONSOLAR SESIÓN GUARDADA
def delete_session_file():
    """Elimina el archivo local de sesión guardada de FusionSolar."""
    if os.path.exists(SESSION_FILE):
        try:
            os.remove(SESSION_FILE)
        except Exception:
            pass


def save_selenium_cookies(driver):
    """Guarda las cookies obtenidas con Selenium para reutilizar la sesión de FusionSolar."""
    cookies = driver.get_cookies()

    session_data = {
        "created_at": now_local().strftime("%Y-%m-%d %H:%M:%S"),
        "cookies": cookies
    }

    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)

    try:
        os.chmod(SESSION_FILE, 0o600)
    except Exception as e:
        print(f"No se pudieron aplicar permisos seguros al archivo de sesión: {e}")


def load_session_from_cookies():
    """Carga una sesión de requests a partir de las cookies guardadas de FusionSolar."""
    if not os.path.exists(SESSION_FILE):
        return None

    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            session_data = json.load(f)

        session = requests.Session()

        for cookie in session_data.get("cookies", []):
            name = cookie.get("name")
            value = cookie.get("value")

            if not name or value is None:
                continue

            session.cookies.set(
                name=name,
                value=value,
                domain=cookie.get("domain"),
                path=cookie.get("path", "/")
            )

        return session

    except Exception as e:
        print(f"No se pudo cargar la sesión guardada: {e}")
        return None


def extract_fusionsolar_quarter_samples(data):
    """Extrae las muestras de potencia real de FusionSolar alineadas a cuartos de hora."""
    product_power = data["data"]["productPower"]
    x_axis = data["data"]["xAxis"]

    samples = {}

    for i in range(len(product_power)):
        if product_power[i] == "--":
            continue

        real_dt = parse_fusionsolar_datetime(x_axis[i])

        if not is_quarter_hour(real_dt):
            continue

        key = real_dt.strftime("%Y-%m-%d %H:%M")
        samples[key] = float(product_power[i])

    if not samples:
        raise RuntimeError("No se encontró ninguna muestra real válida en FusionSolar.")

    return samples


def get_real_production_requests(session):
    """Obtiene la producción real usando una sesión guardada de FusionSolar."""
    tz_offset_minutes = get_local_timezone_offset_minutes()
    tz_hours_str = get_local_timezone_hours_string()
    local_midnight = get_local_midnight_string()

    timestamp = int(time.time() * 1000)

    params = {
        "stationDn": STATION_DN,
        "timeDim": "2",
        "timeZone": tz_hours_str,
        "timeZoneStr": "Europe/Madrid",
        "queryTime": str(timestamp),
        "dateStr": local_midnight,
        "_": str(timestamp)
    }

    headers = {
        "X-Requested-With": "XMLHttpRequest",
        "x-timezone-offset": str(tz_offset_minutes),
        "x-non-renewal-session": "true",
        "Referer": URL_LOGIN,
        "User-Agent": "Mozilla/5.0"
    }

    response = session.get(
        URL_DATA,
        params=params,
        headers=headers,
        timeout=20
    )

    response.raise_for_status()

    try:
        data = response.json()
    except Exception:
        raise RuntimeError(
            "La sesión guardada de FusionSolar ha caducado o ya no es válida."
        )

    if not data.get("success"):
        raise RuntimeError(f"FusionSolar respondió sin éxito usando sesión guardada: {data}")

    return extract_fusionsolar_quarter_samples(data)



# FUSIONSOLAR CON SELENIUM
def login_fusionsolar():
    """Inicia sesión en FusionSolar con Selenium y guarda las cookies generadas."""
    delete_session_file()

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    service = Service("/usr/bin/geckodriver")
    driver = webdriver.Firefox(service=service, options=options)

    driver.get(URL_LOGIN)
    time.sleep(5)

    user_box = driver.find_element(By.ID, "username")
    user_box.clear()
    user_box.send_keys(USERNAME)

    pass_box = driver.find_element(By.ID, "value")
    pass_box.clear()
    pass_box.send_keys(PASSWORD)
    pass_box.send_keys(Keys.RETURN)

    time.sleep(12)

    save_selenium_cookies(driver)

    return driver


def get_real_production_selenium(driver):
    """Obtiene la producción real de FusionSolar desde una sesión activa de Selenium."""
    tz_offset_minutes = get_local_timezone_offset_minutes()
    tz_hours_str = get_local_timezone_hours_string()
    local_midnight = get_local_midnight_string()

    timestamp = int(time.time() * 1000)

    url = (
        f"{URL_DATA}?"
        f"stationDn={STATION_DN}"
        f"&timeDim=2"
        f"&timeZone={tz_hours_str}"
        f"&timeZoneStr=Europe/Madrid"
        f"&queryTime={timestamp}"
        f"&dateStr={local_midnight}"
        f"&_={timestamp}"
    )

    script = f"""
    return fetch("{url}", {{
        method: "GET",
        credentials: "include",
        headers: {{
            "X-Requested-With": "XMLHttpRequest",
            "x-timezone-offset": "{tz_offset_minutes}",
            "x-non-renewal-session": "true"
        }}
    }}).then(r => r.text());
    """

    response_text = driver.execute_script(script)

    try:
        data = json.loads(response_text)
    except Exception:
        raise RuntimeError(
            "FusionSolar no devolvió JSON dentro del navegador. "
            "Inicio respuesta: " + str(response_text)[:300]
        )

    if not data.get("success"):
        raise RuntimeError(f"FusionSolar respondió sin éxito: {data}")

    return extract_fusionsolar_quarter_samples(data)


def get_real_production():
    """Obtiene la producción real priorizando la sesión guardada y renovándola si es necesario."""
    session = load_session_from_cookies()

    if session is not None:
        try:
            fusion_samples = get_real_production_requests(session)
            print("Sesión FusionSolar: sesión guardada usada correctamente.")
            return fusion_samples, None, "sesion_guardada"
        except Exception:
            print("Sesión FusionSolar: sesión guardada caducada, se renueva automáticamente.")

    driver = login_fusionsolar()
    fusion_samples = get_real_production_selenium(driver)

    print("Sesión FusionSolar: renovada correctamente.")
    return fusion_samples, driver, "sesion_renovada"


# OPEN-METEO
def extract_open_meteo_arrays(data):
    """Extrae y valida los datos de irradiancia, temperatura y tiempo de Open-Meteo."""
    minutely = data.get("minutely_15", {})

    times = minutely.get("time", [])
    gti_values = minutely.get("global_tilted_irradiance_instant", [])
    temp_values = minutely.get("temperature_2m", [])

    if not times or not gti_values or not temp_values:
        raise RuntimeError("Open-Meteo no devolvió datos suficientes.")

    if not (len(times) == len(gti_values) == len(temp_values)):
        raise RuntimeError("Los arrays de Open-Meteo no tienen la misma longitud.")

    return times, gti_values, temp_values


def save_open_meteo_cache(data):
    """Guarda en caché local los datos válidos recibidos desde Open-Meteo."""
    try:
        times, gti_values, temp_values = extract_open_meteo_arrays(data)

        cache_payload = {
            "created_at": now_local().isoformat(),
            "source": "open-meteo",
            "params": OPEN_METEO_PARAMS,
            "data": {
                "time": times,
                "global_tilted_irradiance_instant": gti_values,
                "temperature_2m": temp_values
            }
        }

        tmp_file = OPEN_METEO_CACHE_FILE + ".tmp"

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cache_payload, f, ensure_ascii=False, indent=2)

        os.replace(tmp_file, OPEN_METEO_CACHE_FILE)

    except Exception as e:
        print(f"No se pudo guardar la caché de Open-Meteo: {e}")


def load_open_meteo_cache():
    """Carga la caché local de Open-Meteo si existe, es válida y contiene datos útiles."""
    if not os.path.exists(OPEN_METEO_CACHE_FILE):
        print("Open-Meteo: no existe caché local.")
        return None

    try:
        with open(OPEN_METEO_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)

        created_at = cache.get("created_at")
        if not created_at:
            print("Open-Meteo: caché sin fecha de creación.")
            return None

        created_dt = datetime.fromisoformat(created_at)

        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=LOCAL_TZ)

        age = now_local() - created_dt.astimezone(LOCAL_TZ)
        age_minutes = age.total_seconds() / 60.0

        if age_minutes > OPEN_METEO_CACHE_MAX_AGE_MINUTES:
            print(f"Open-Meteo: caché caducada. Edad: {age_minutes:.1f} minutos.")
            return None

        if cache.get("params") != OPEN_METEO_PARAMS:
            print("Open-Meteo: la caché no coincide con los parámetros actuales.")
            return None

        cached_data = cache.get("data", {})

        times = cached_data.get("time", [])
        gti_values = cached_data.get("global_tilted_irradiance_instant", [])
        temp_values = cached_data.get("temperature_2m", [])

        if not times or not gti_values or not temp_values:
            print("Open-Meteo: caché sin datos suficientes.")
            return None

        if not (len(times) == len(gti_values) == len(temp_values)):
            print("Open-Meteo: arrays de caché con longitudes distintas.")
            return None

        target_dt = floor_to_15_minutes(now_local().replace(tzinfo=None))

        expected_keys = [
            (target_dt - timedelta(minutes=15 * step)).strftime("%Y-%m-%dT%H:%M")
            for step in range(MAX_FALLBACK_STEPS + 1)
        ]

        if not any(key in times for key in expected_keys):
            print("Open-Meteo: la caché existe, pero no contiene ningún bloque útil para la hora actual.")
            return None

        return times, gti_values, temp_values

    except Exception as e:
        print(f"Open-Meteo: no se pudo leer la caché local: {type(e).__name__}")
        return None


def get_theoretical_data():
    """Obtiene los datos teóricos desde Open-Meteo o desde la caché local si la API falla."""
    last_error = None

    for attempt in range(1, OPEN_METEO_MAX_RETRIES + 1):
        try:
            response = requests.get(
                OPEN_METEO_URL,
                params=OPEN_METEO_PARAMS,
                timeout=OPEN_METEO_TIMEOUT_SECONDS
            )

            response.raise_for_status()

            try:
                data = response.json()
            except Exception:
                raise RuntimeError("Open-Meteo no devolvió JSON válido.")

            times, gti_values, temp_values = extract_open_meteo_arrays(data)

            save_open_meteo_cache(data)

            print("Open-Meteo: datos obtenidos correctamente.")
            print("Caché Open-Meteo usada: no")

            return times, gti_values, temp_values, False

        except Exception as e:
            last_error = e

            if attempt < OPEN_METEO_MAX_RETRIES:
                time.sleep(OPEN_METEO_RETRY_SLEEP_SECONDS)

    cached = load_open_meteo_cache()

    if cached is not None:
        times, gti_values, temp_values = cached

        print(f"Open-Meteo: API no disponible tras {OPEN_METEO_MAX_RETRIES} intentos. Se usa caché local.")
        print("Caché Open-Meteo usada: sí")

        return times, gti_values, temp_values, True

    raise RuntimeError(
        "Open-Meteo no disponible y no hay caché válida. "
        f"Último error técnico: {type(last_error).__name__}"
    )


def find_latest_common_sample(fusion_samples):
    """Busca el bloque temporal más reciente común entre FusionSolar y Open-Meteo."""
    times, gti_values, temp_values, open_meteo_cache_used = get_theoretical_data()

    open_meteo_samples = {}

    for i, time_key in enumerate(times):
        gti = float(gti_values[i])
        temperature = float(temp_values[i])

        open_meteo_samples[time_key] = {
            "gti": gti,
            "temperature": temperature,
            "theoretical_power_kw": INSTALLED_POWER_KWP * (gti / 1000.0)
        }

    target_dt = floor_to_15_minutes(now_local().replace(tzinfo=None))

    for step in range(MAX_FALLBACK_STEPS + 1):
        candidate_dt = target_dt - timedelta(minutes=15 * step)

        fusion_key = candidate_dt.strftime("%Y-%m-%d %H:%M")
        meteo_key = candidate_dt.strftime("%Y-%m-%dT%H:%M")

        if fusion_key in fusion_samples and meteo_key in open_meteo_samples:
            meteo = open_meteo_samples[meteo_key]

            return {
                "real_time_str": fusion_key,
                "real_power_kw": fusion_samples[fusion_key],
                "requested_time_key": meteo_key,
                "theoretical_time_str": meteo_key,
                "gti": meteo["gti"],
                "temperature": meteo["temperature"],
                "theoretical_power_kw": meteo["theoretical_power_kw"],
                "open_meteo_cache_used": open_meteo_cache_used,
                "used_fallback": step > 0,
                "fallback_steps_used": step
            }

    raise RuntimeError(
        "No se encontró ningún bloque común entre FusionSolar y Open-Meteo "
        f"en los últimos {MAX_FALLBACK_STEPS * 15} minutos."
    )


# COMPARACIÓN
def compare_real_vs_theoretical(real_power_kw, theoretical_power_kw):
    """Calcula el ratio y la diferencia entre la potencia real y la potencia teórica."""
    if theoretical_power_kw <= 0:
        return None, None
    return real_power_kw / theoretical_power_kw, real_power_kw - theoretical_power_kw


def classify_instant_status(ratio, gti):
    """Clasifica el estado instantáneo de producción según el ratio y la irradiancia."""
    if ratio is None:
        return "invalid_theoretical"
    if gti < MIN_GTI_FOR_ALERTS:
        return "low_irradiance"
    if ratio < 0.80:
        return "warning"
    return "normal"


def classify_window_status(window_ratio, window_gti_avg):
    """Clasifica el estado de la ventana temporal según el ratio medio y la irradiancia media."""
    if window_ratio is None:
        return "insufficient_data"
    if window_gti_avg is None or window_gti_avg < MIN_GTI_FOR_ALERTS:
        return "low_irradiance"
    if window_ratio < 0.85:
        return "warning"
    return "normal"


# VENTANA
def safe_float(value):
    """Convierte un valor a número decimal de forma segura."""
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except ValueError:
        return None


def get_valid_rows_for_window(rows):
    """Filtra las filas válidas del histórico para calcular la comparación por ventana."""
    valid = []

    for row in rows:
        real_power = safe_float(row.get("real_power_kw"))
        theoretical_power = safe_float(row.get("theoretical_power_kw"))
        instant_ratio = safe_float(row.get("instant_ratio"))
        gti = safe_float(row.get("gti_w_m2"))

        if real_power is None or theoretical_power is None or theoretical_power <= 0:
            continue
        if instant_ratio is None:
            continue
        if gti is None or gti < MIN_GTI_FOR_ALERTS:
            continue

        valid.append({
            "real_time": row.get("real_time"),
            "execution_time": row.get("execution_time"),
            "real_power_kw": real_power,
            "theoretical_power_kw": theoretical_power,
            "gti_w_m2": gti,
            "instant_ratio": instant_ratio,
            "instant_diff_kw": safe_float(row.get("instant_diff_kw"))
        })

    return valid


def calculate_window_metrics_from_history(current_sample):
    """Calcula las métricas medias de producción usando la muestra actual y el histórico reciente."""
    rows = read_history_rows()
    valid_rows = get_valid_rows_for_window(rows)

    current_is_valid = (
        current_sample["real_power_kw"] is not None
        and current_sample["theoretical_power_kw"] is not None
        and current_sample["theoretical_power_kw"] > 0
        and current_sample["instant_ratio"] is not None
        and current_sample["gti"] is not None
        and current_sample["gti"] >= MIN_GTI_FOR_ALERTS
    )

    if current_is_valid:
        valid_rows = [r for r in valid_rows if r.get("real_time") != current_sample["real_time"]]
        valid_rows.append({
            "real_time": current_sample["real_time"],
            "execution_time": current_sample["execution_time"],
            "real_power_kw": current_sample["real_power_kw"],
            "theoretical_power_kw": current_sample["theoretical_power_kw"],
            "gti_w_m2": current_sample["gti"],
            "instant_ratio": current_sample["instant_ratio"],
            "instant_diff_kw": current_sample["instant_diff_kw"]
        })

    valid_rows.sort(key=lambda r: r.get("real_time", ""))

    if len(valid_rows) < WINDOW_SIZE_SAMPLES:
        return {
            "window_real_avg_kw": None,
            "window_theoretical_avg_kw": None,
            "window_gti_avg_w_m2": None,
            "window_ratio": None,
            "window_diff_kw": None,
            "window_status": "insufficient_data",
            "samples_used": len(valid_rows)
        }

    selected = valid_rows[-WINDOW_SIZE_SAMPLES:]

    real_avg = sum(r["real_power_kw"] for r in selected) / WINDOW_SIZE_SAMPLES
    theoretical_avg = sum(r["theoretical_power_kw"] for r in selected) / WINDOW_SIZE_SAMPLES
    gti_avg = sum(r["gti_w_m2"] for r in selected) / WINDOW_SIZE_SAMPLES

    window_ratio = real_avg / theoretical_avg if theoretical_avg > 0 else None
    window_diff = real_avg - theoretical_avg if theoretical_avg > 0 else None
    window_status = classify_window_status(window_ratio, gti_avg)

    return {
        "window_real_avg_kw": real_avg,
        "window_theoretical_avg_kw": theoretical_avg,
        "window_gti_avg_w_m2": gti_avg,
        "window_ratio": window_ratio,
        "window_diff_kw": window_diff,
        "window_status": window_status,
        "samples_used": WINDOW_SIZE_SAMPLES
    }


# PERSISTENCIA
def csv_bool(value):
    """Convierte un valor leído del CSV a booleano."""
    return str(value).strip().lower() in ("true", "1", "yes", "si", "sí")


def get_saved_final_status(row):
    """Reconstruye el estado final guardado en una ejecución anterior."""
    if csv_bool(row.get("persistent_alert")):
        return "alert"

    if csv_bool(row.get("persistent_warning")):
        return "warning"

    return str(row.get("instant_status", "")).strip().lower()


def calculate_persistent_anomaly_from_history(current_row):
    """Calcula si existe una anomalía persistente a partir del histórico de ejecuciones anteriores."""
    current_real_time = current_row["real_time"]
    current_status = str(current_row.get("instant_status", "")).strip().lower()

    rows = read_history_rows()

    previous_rows = [
        normalize_row(row)
        for row in rows
        if row.get("real_time") != current_real_time
    ]

    previous_rows.sort(key=lambda r: r.get("real_time", ""))

    consecutive_warning_or_alert = 0

    for row in reversed(previous_rows):
        previous_final_status = get_saved_final_status(row)

        if previous_final_status in ("warning", "alert"):
            consecutive_warning_or_alert += 1
        else:
            break

    if current_status in ("warning", "alert"):
        consecutive_warning_or_alert += 1

        persistent_alert = consecutive_warning_or_alert >= PERSISTENT_ALERT_THRESHOLD
        persistent_warning = not persistent_alert

        final_status = "alert" if persistent_alert else "warning"

    else:
        consecutive_warning_or_alert = 0
        persistent_alert = False
        persistent_warning = False
        final_status = current_status

    return {
        "consecutive_window_alerts": consecutive_warning_or_alert if persistent_alert else 0,
        "consecutive_window_warning_or_alert": consecutive_warning_or_alert,
        "persistent_alert": persistent_alert,
        "persistent_warning": persistent_warning,
        "final_status": final_status
    }


# MAIN
def main():
    """Ejecuta el diagnóstico solar completo y guarda el resultado en el histórico."""
    driver = None

    try:
        execution_time = now_local().strftime("%Y-%m-%d %H:%M:%S")

        fusion_samples, driver, _ = get_real_production()
        matched_data = find_latest_common_sample(fusion_samples)

        real_time_str = matched_data["real_time_str"]
        real_power_kw = matched_data["real_power_kw"]
        requested_time_key = matched_data["requested_time_key"]
        theoretical_time_str = matched_data["theoretical_time_str"]
        gti = matched_data["gti"]
        temperature = matched_data["temperature"]
        theoretical_power_kw = matched_data["theoretical_power_kw"]
        open_meteo_cache_used = matched_data["open_meteo_cache_used"]
        used_fallback = matched_data["used_fallback"]
        fallback_steps_used = matched_data["fallback_steps_used"]

        instant_ratio, instant_diff_kw = compare_real_vs_theoretical(real_power_kw, theoretical_power_kw)
        instant_status = classify_instant_status(instant_ratio, gti)

        real_dt = parse_fusionsolar_datetime(real_time_str)

        current_sample = {
            "execution_time": execution_time,
            "real_time": real_time_str,
            "real_power_kw": real_power_kw,
            "theoretical_power_kw": theoretical_power_kw,
            "gti": gti,
            "instant_ratio": instant_ratio,
            "instant_diff_kw": instant_diff_kw
        }

        window_data = calculate_window_metrics_from_history(current_sample)

        window_real_avg_kw = window_data["window_real_avg_kw"]
        window_theoretical_avg_kw = window_data["window_theoretical_avg_kw"]
        window_gti_avg_w_m2 = window_data["window_gti_avg_w_m2"]
        window_ratio = window_data["window_ratio"]
        window_diff_kw = window_data["window_diff_kw"]
        window_status = window_data["window_status"]
        samples_used = window_data["samples_used"]

        current_row_for_persistence = {
            "execution_time": execution_time,
            "real_time": real_time_str,
            "requested_time_key": requested_time_key,
            "theoretical_time": theoretical_time_str,
            "open_meteo_cache_used": str(open_meteo_cache_used),
            "used_fallback": str(used_fallback),
            "fallback_steps_used": str(fallback_steps_used),
            "real_power_kw": f"{real_power_kw:.6f}",
            "gti_w_m2": f"{gti:.6f}",
            "temperature_c": f"{temperature:.6f}",
            "theoretical_power_kw": f"{theoretical_power_kw:.6f}",
            "instant_ratio": f"{instant_ratio:.6f}" if instant_ratio is not None else "",
            "instant_diff_kw": f"{instant_diff_kw:.6f}" if instant_diff_kw is not None else "",
            "instant_status": instant_status,
            "window_real_avg_kw": f"{window_real_avg_kw:.6f}" if window_real_avg_kw is not None else "",
            "window_theoretical_avg_kw": f"{window_theoretical_avg_kw:.6f}" if window_theoretical_avg_kw is not None else "",
            "window_gti_avg_w_m2": f"{window_gti_avg_w_m2:.6f}" if window_gti_avg_w_m2 is not None else "",
            "window_ratio": f"{window_ratio:.6f}" if window_ratio is not None else "",
            "window_diff_kw": f"{window_diff_kw:.6f}" if window_diff_kw is not None else "",
            "window_status": window_status,
            "consecutive_window_alerts": "",
            "consecutive_window_warning_or_alert": "",
            "persistent_alert": "",
            "persistent_warning": "",
            "algorithm_version": ALGORITHM_VERSION
        }

        persistence_data = calculate_persistent_anomaly_from_history(current_row_for_persistence)

        consecutive_window_alerts = persistence_data["consecutive_window_alerts"]
        consecutive_window_warning_or_alert = persistence_data["consecutive_window_warning_or_alert"]
        persistent_alert = persistence_data["persistent_alert"]
        persistent_warning = persistence_data["persistent_warning"]
        final_status = persistence_data["final_status"]

        print("\nDiagnóstico solar:")
        print("Producción real:")
        print(f"  Hora real FusionSolar: {real_time_str}")
        print(f"  Real alineada a 15 min: {'sí' if is_quarter_hour(real_dt) else 'no'}")
        print(f"  Potencia real: {real_power_kw:.3f} kW")

        print("\nAlineación temporal:")
        print(f"  Bloque común usado: {real_time_str}")
        print(f"  Bloque real objetivo: {requested_time_key}")
        print(f"  Bloque teórico encontrado: {theoretical_time_str}")
        print(f"  Fallback aplicado: {'sí' if used_fallback else 'no'}")
        print(f"  Pasos de fallback: {fallback_steps_used}")

        print("\nFuente meteorológica:")
        print(f"  Caché Open-Meteo usada: {'sí' if open_meteo_cache_used else 'no'}")

        print("\nProducción teórica:")
        print(f"  Irradiancia (GTI): {gti:.1f} W/m²")
        print(f"  Temperatura: {temperature:.1f} °C")
        print(f"  Potencia teórica: {theoretical_power_kw:.3f} kW")

        print("\nComparación instantánea:")
        if instant_ratio is None:
            print("  No se pudo calcular ratio.")
        else:
            print(f"  Diferencia instantánea real - teórica: {instant_diff_kw:.3f} kW")
            print(f"  Ratio instantáneo real/teórica: {instant_ratio:.3f}")

        print(f"  Estado final: {final_status}")

        print("\nComparación por ventana (30 min):")
        print(f"  Muestras válidas usadas: {samples_used}")

        if window_ratio is None:
            print("  Aún no hay suficientes datos válidos para calcular la ventana.")
        else:
            print(f"  Potencia real media: {window_real_avg_kw:.3f} kW")
            print(f"  Potencia teórica media: {window_theoretical_avg_kw:.3f} kW")
            print(f"  GTI media: {window_gti_avg_w_m2:.1f} W/m²")
            print(f"  Diferencia media real - teórica: {window_diff_kw:.3f} kW")
            print(f"  Ratio medio real/teórica: {window_ratio:.3f}")

        print(f"  Estado ventana: {window_status}")

        print("\nPersistencia de anomalía:")
        print(f"  Alertas consecutivas de ventana: {consecutive_window_alerts}")
        print(f"  Warnings/alerts consecutivos: {consecutive_window_warning_or_alert}")
        print(f"  Persistent warning: {'sí' if persistent_warning else 'no'}")
        print(f"  Persistent alert: {'sí' if persistent_alert else 'no'}")

        if os.environ.get("DEBUG_NO_SAVE") == "1":
            print("\nRegistro histórico: no guardado, ejecución de diagnóstico.")
        else:
            save_action = upsert_history_row(
                execution_time, real_time_str, requested_time_key, theoretical_time_str,
                open_meteo_cache_used,
                used_fallback, fallback_steps_used, real_power_kw, gti, temperature,
                theoretical_power_kw, instant_ratio, instant_diff_kw, instant_status,
                window_real_avg_kw, window_theoretical_avg_kw, window_gti_avg_w_m2,
                window_ratio, window_diff_kw, window_status, consecutive_window_alerts,
                consecutive_window_warning_or_alert, persistent_alert, persistent_warning
            )

            if save_action == "inserted":
                print("\nRegistro histórico: guardado correctamente.")
            else:
                print("\nRegistro histórico: actualizado correctamente.")

        print("Fin del diagnóstico.")

    except Exception as e:
        print(f"ERROR: {type(e).__name__}")

        try:
            save_action = upsert_error_history_row(e)
            print("Registro de error guardado correctamente.")
        except Exception:
            print("ERROR adicional guardando el fallo en el historial.")

    finally:
        if driver is not None:
            driver.quit()


if __name__ == "__main__":
    main()
