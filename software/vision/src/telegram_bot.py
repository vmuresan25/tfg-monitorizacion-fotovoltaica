from pathlib import Path
from datetime import datetime
import json
import logging
import asyncio
import sys
import os
import shutil
import re
import traceback

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    MessageHandler,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    filters,
)


#BASE DIR
BASE_DIR = Path(__file__).resolve().parent
SOLAR_HOURS_FILE = BASE_DIR / "sun_times_cache.json"

#CONFIG
TOKEN = os.environ.get("BOT_TOKEN", "").strip()

INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
RESULT_DIR = BASE_DIR / "result"
DEBUG_DIR = BASE_DIR / "debug"
DEBUG_RESULT_DIR = BASE_DIR / "debug_result"

MAX_RESULT_IMAGES = 50
MAX_DEBUG_DIRS = 50
MAX_DEBUG_RESULT_DIRS = 50

EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def ts():
    """Genera una marca temporal única para nombrar archivos."""
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


#HELPERS
def recortar(txt: str, n: int = 3500) -> str:
    """Recorta un texto largo manteniendo solo los últimos caracteres indicados."""
    txt = (txt or "").strip()
    return txt[-n:] if len(txt) > n else txt


def rm_tree(path: Path) -> bool:
    """Elimina un archivo o una carpeta completa y devuelve si la operación tuvo éxito."""
    try:
        if path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        return True
    except Exception:
        return False


def ultima_archivo(folder: Path):
    """Obtiene el archivo de imagen más reciente dentro de una carpeta."""
    if not folder.exists():
        return None
    files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS]
    if not files:
        return None
    return max(files, key=lambda p: p.stat().st_mtime)


def limpiar_antiguos(directorio: Path, max_elementos: int, es_directorio: bool = False):
    """Elimina archivos o carpetas antiguas cuando se supera el límite configurado."""
    if not directorio.exists():
        return

    if es_directorio:
        elementos = [p for p in directorio.iterdir() if p.is_dir()]
    else:
        elementos = [p for p in directorio.iterdir() if p.is_file() and p.suffix.lower() in EXTS]

    if len(elementos) > max_elementos:
        elementos.sort(key=lambda x: x.stat().st_mtime)
        for p in elementos[: len(elementos) - max_elementos]:
            rm_tree(p)


def comprobar_horario_solar():
    """Comprueba si la ejecución manual del monitor está dentro del horario solar permitido."""
    try:
        with open(SOLAR_HOURS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        sunrise = datetime.fromisoformat(data["sunrise"])
        sunset = datetime.fromisoformat(data["sunset"])
        now = datetime.now(sunrise.tzinfo)

        sunrise_txt = sunrise.strftime("%H:%M")
        sunset_txt = sunset.strftime("%H:%M")
        now_txt = now.strftime("%H:%M")

        if data.get("date") != now.date().isoformat():
            return False, (
                "Monitor solar manual no ejecutado.\n\n"
                "El archivo de horas solares no corresponde a hoy.\n"
                f"Amanecer del archivo: {sunrise_txt}\n"
                f"Atardecer del archivo: {sunset_txt}\n"
                f"Hora actual: {now_txt}"
            )

        if sunrise <= now <= sunset:
            return True, ""

        return False, (
            "Monitor solar manual no ejecutado.\n\n"
            "Estamos fuera de las horas solares de hoy.\n"
            f"Amanecer: {sunrise_txt}\n"
            f"Atardecer: {sunset_txt}\n"
            f"Hora actual: {now_txt}"
        )

    except FileNotFoundError:
        return False, (
            "Monitor solar manual no ejecutado.\n\n"
            f"No se encontró el archivo de horas solares:\n{SOLAR_HOURS_FILE}"
        )

    except Exception as e:
        return False, (
            "Monitor solar manual no ejecutado.\n\n"
            "No se pudo leer correctamente el archivo de horas solares.\n"
            f"Error: {type(e).__name__}"
        )


async def run_script_async(
    script_name: str,
    skip_telegram: bool = False,
    debug_no_save: bool = False
):
    """Ejecuta un script Python de forma asíncrona y devuelve su código de salida y resultado."""
    env = os.environ.copy()

    env.pop("SKIP_TELEGRAM_SEND", None)

    if skip_telegram:
        env["SKIP_TELEGRAM_SEND"] = "1"

    if debug_no_save:
        env["DEBUG_NO_SAVE"] = "1"

    script_path = BASE_DIR / script_name

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BASE_DIR),
        env=env,
    )

    stdout, stderr = await process.communicate()

    out = (stdout.decode(errors="replace") or "")
    if stderr:
        out += "\n" + stderr.decode(errors="replace")

    return process.returncode, out.strip()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Registra los errores internos producidos durante la ejecución del bot."""
    logging.error("Error en el bot: %s", context.error)
    traceback.print_exception(type(context.error), context.error, context.error.__traceback__)


#START
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el mensaje inicial del bot y el botón de acceso al menú principal."""
    keyboard = [[InlineKeyboardButton("Abrir menú", callback_data="main")]]
    await update.message.reply_text(
        "Bot activo. Pulsa para abrir el menú:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


#MENU
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestiona las acciones del menú interactivo del bot de Telegram."""
    query = update.callback_query
    data = query.data

    await query.answer()

    if data == "main":
        await query.edit_message_text(
            "Menú principal:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Solar", callback_data="solar")],
                [InlineKeyboardButton("Limpieza", callback_data="clean")],
                [InlineKeyboardButton("Debug", callback_data="debug_menu")],
            ]),
        )
        return

    if data == "solar":
        await query.edit_message_text(
            "Menú Solar:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Ejecutar monitor", callback_data="solar_run")],
                [InlineKeyboardButton("Volver", callback_data="main")],
            ]),
        )
        return

    if data == "clean":
        await query.edit_message_text(
            "Limpieza:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Limpiar result", callback_data="result_clear")],
                [InlineKeyboardButton("Limpiar debug", callback_data="debug_clear")],
                [InlineKeyboardButton("Limpiar debug_result", callback_data="dbg_clear")],
                [InlineKeyboardButton("Volver", callback_data="main")],
            ]),
        )
        return

    if data == "debug_menu":
        await query.edit_message_text(
            "Debug:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Estado completo", callback_data="debug_full_status")],
                [InlineKeyboardButton("Volver", callback_data="main")],
            ]),
        )
        return

    if data == "debug_full_status":
        msg = await query.message.reply_text("Obteniendo datos...")

        rc, out = await run_script_async(
            "logs_comparation.py",
            debug_no_save=True
        )

        texto = recortar(out) if out else "Sin salida."
        await msg.edit_text(f"```\n{texto}\n```", parse_mode="Markdown")
        return

    if data == "solar_run":
        dentro_horario, mensaje_horario = comprobar_horario_solar()

        if not dentro_horario:
            await query.message.reply_text(mensaje_horario)
            return

        await query.message.reply_text("Ejecutando monitor solar manual...")

        rc, out = await run_script_async(
            "run_solar_monitor.py",
            skip_telegram=False,
            debug_no_save=True
        )

        if rc != 0 or "ERROR:" in out:
            texto = recortar(out) if out else "Error ejecutando el monitor solar."
            await query.message.reply_text(f"```\n{texto}\n```", parse_mode="Markdown")

        return

    if data == "result_clear":
        RESULT_DIR.mkdir(exist_ok=True)
        n = sum(1 for p in RESULT_DIR.iterdir() if p.is_file() and rm_tree(p))
        await query.message.reply_text(f"Result limpiado: {n}")
        return

    if data == "debug_clear":
        DEBUG_DIR.mkdir(exist_ok=True)
        n = sum(1 for p in DEBUG_DIR.iterdir() if p.is_dir() and rm_tree(p))
        await query.message.reply_text(f"Debug limpiado: {n}")
        return

    if data == "dbg_clear":
        DEBUG_RESULT_DIR.mkdir(exist_ok=True)
        n = sum(1 for p in DEBUG_RESULT_DIR.iterdir() if p.is_dir() and rm_tree(p))
        await query.message.reply_text(f"Debug result limpiado: {n}")
        return


#FOTO / DOCUMENTO
async def handle_incoming_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Procesa las imágenes recibidas por Telegram y ejecuta el análisis de visión artificial."""
    if not update.message:
        return

    tiene_photo = bool(update.message.photo)
    tiene_document = bool(update.message.document)

    if not tiene_photo and not tiene_document:
        return


    INPUT_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)
    RESULT_DIR.mkdir(exist_ok=True)
    DEBUG_DIR.mkdir(exist_ok=True)
    DEBUG_RESULT_DIR.mkdir(exist_ok=True)

    limpiar_antiguos(RESULT_DIR, MAX_RESULT_IMAGES, es_directorio=False)
    limpiar_antiguos(DEBUG_DIR, MAX_DEBUG_DIRS, es_directorio=True)
    limpiar_antiguos(DEBUG_RESULT_DIR, MAX_DEBUG_RESULT_DIRS, es_directorio=True)

    try:
        if update.message.photo:
            file_obj = update.message.photo[-1]
        else:
            doc = update.message.document
            mime = (doc.mime_type or "").lower()
            filename = (doc.file_name or "").lower()

            es_imagen = mime.startswith("image/") or any(filename.endswith(ext) for ext in EXTS)
            if not es_imagen:
                await update.message.reply_text("Ese documento no parece ser una imagen.")
                return

            file_obj = doc

        tg_file = await context.bot.get_file(file_obj.file_id)

        in_path = INPUT_DIR / f"{ts()}.jpg"
        await tg_file.download_to_drive(str(in_path))

        msg = await update.message.reply_text("Procesando imagen...")

        rc, out = await run_script_async("crop_panel_roboflow.py")
        img = ultima_archivo(OUTPUT_DIR)

        if rc == 0 and img and img.exists():
            with open(img, "rb") as f:
                await update.message.reply_photo(photo=f)
        else:
            await msg.edit_text(f"Error recorte:\n{recortar(out)}" if out else "Error recorte: sin salida")
            return

        rc2, out2 = await run_script_async("detect_dirt_objects.py")
        img2 = ultima_archivo(RESULT_DIR)

        if rc2 == 0 and img2 and img2.exists():
            with open(img2, "rb") as f:
                await update.message.reply_photo(photo=f)

            match = re.search(r"Suciedad:\s*([0-9.]+(?:%)?)", out2 or "")
            if match:
                porcentaje = match.group(1)
                if not porcentaje.endswith("%"):
                    porcentaje += "%"
                await msg.edit_text(f"OK. Suciedad: {porcentaje}")
            else:
                texto = recortar(out2, 1000) if out2 else "OK."
                await msg.edit_text(texto if texto else "OK.")
        else:
            await msg.edit_text(f"Error detección:\n{recortar(out2)}" if out2 else "Error detección: sin salida")

    except Exception as e:
        traceback.print_exc()
        try:
            await update.message.reply_text(f"Error interno: {e}")
        except Exception:
            pass


#MAIN
def main():
    """Configura e inicia el bot de Telegram junto con sus manejadores principales."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    if not TOKEN:
        raise SystemExit("Falta BOT_TOKEN")

    app = Application.builder().token(TOKEN).build()

    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))

    app.add_handler(MessageHandler(filters.ALL, handle_incoming_message))

    logging.info("Bot iniciado")
    app.run_polling()


if __name__ == "__main__":
    main()
