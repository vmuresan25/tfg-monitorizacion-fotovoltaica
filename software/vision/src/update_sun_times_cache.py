import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


LATITUDE = 36.8039
LONGITUDE = -2.617
TIMEZONE = "Europe/Madrid"

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

BASE_DIR = Path("/home/vasi/Escritorio/vision/src")
SUN_CACHE_FILE = BASE_DIR / "sun_times_cache.json"


def update_sun_times_cache():
    """Actualiza la caché local con las horas de amanecer y atardecer del día correspondiente."""
    BASE_DIR.mkdir(parents=True, exist_ok=True)

    tz = ZoneInfo(TIMEZONE)
    now = datetime.now(tz)

    # Si se ejecuta a partir de las 23:00, prepara la caché del día siguiente.
    # Si se ejecuta manualmente durante el día, prepara la caché del día actual.
    if now.hour >= 23:
        target_date = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        target_date = now.strftime("%Y-%m-%d")

    params = {
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "daily": "sunrise,sunset",
        "timezone": TIMEZONE,
        "start_date": target_date,
        "end_date": target_date,
    }

    r = requests.get(OPEN_METEO_URL, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    sunrise = datetime.fromisoformat(data["daily"]["sunrise"][0]).replace(tzinfo=tz)
    sunset = datetime.fromisoformat(data["daily"]["sunset"][0]).replace(tzinfo=tz)

    cache_data = {
        "date": target_date,
        "sunrise": sunrise.isoformat(),
        "sunset": sunset.isoformat()
    }

    with open(SUN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)

    print("Caché de horas solares actualizada correctamente.")
    print(f"Archivo: {SUN_CACHE_FILE}")
    print(f"Fecha guardada: {target_date}")
    print(f"Sunrise: {sunrise}")
    print(f"Sunset: {sunset}")


if __name__ == "__main__":
    update_sun_times_cache()
