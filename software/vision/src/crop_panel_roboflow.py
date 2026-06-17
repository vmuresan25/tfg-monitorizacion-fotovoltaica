from pathlib import Path
from datetime import datetime
import json

import cv2
import numpy as np
from inference_sdk import InferenceHTTPClient


#CONFIGURACIÓN
BASE_DIR = Path(__file__).resolve().parent

SOURCE_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
DEBUG_DIR = BASE_DIR / "debug"

MAX_IMGS_OUTPUT = 200
MAX_DIRS_DEBUG = 60

EXTS_IMG = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

API_KEY = "RFWGKH08zG4HGcytBGXz"
MODEL_ID = "solarpanel2-nip8i/1"



def asegurar_dir(ruta: Path) -> Path:
    """Crea una carpeta si no existe."""
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def listar_imgs(carpeta: Path):
    """Devuelve las imágenes válidas encontradas en una carpeta."""
    if not carpeta.exists():
        return []
    return sorted([p for p in carpeta.rglob("*") if p.is_file() and p.suffix.lower() in EXTS_IMG])


def ultima_img(carpeta: Path):
    """Obtiene la imagen más reciente de una carpeta."""
    imgs = listar_imgs(carpeta)
    return max(imgs, key=lambda x: x.stat().st_mtime) if imgs else None


def imread_unicode(path: Path):
    """Lee una imagen desde una ruta compatible con caracteres especiales."""
    try:
        data = np.fromfile(str(path), dtype=np.uint8)
        if data.size == 0:
            return None
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception:
        return None


def imwrite_unicode(path: Path, img: np.ndarray) -> bool:
    """Guarda una imagen en una ruta compatible con caracteres especiales."""
    try:
        ext = path.suffix if path.suffix else ".png"
        ok, encoded = cv2.imencode(ext, img)
        if not ok:
            return False
        encoded.tofile(str(path))
        return True
    except Exception:
        return False


def guardar_dbg(dbg: Path | None, nombre: str, img: np.ndarray):
    """Guarda una imagen auxiliar de depuración."""
    if dbg is not None:
        ok = imwrite_unicode(dbg / nombre, img)
        if not ok:
            print(f"[WARN] No se pudo guardar debug: {dbg / nombre}")


def guardar_json_dbg(dbg: Path | None, nombre: str, data: dict):
    """Guarda datos de depuración en formato JSON."""
    if dbg is not None:
        try:
            with open(dbg / nombre, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[WARN] No se pudo guardar JSON debug {dbg / nombre}: {e}")


def siguiente_dbg(raiz: Path | None) -> Path | None:
    """Crea una nueva carpeta numerada para depuración."""
    if raiz is None:
        return None

    raiz.mkdir(parents=True, exist_ok=True)

    nums = []
    for p in raiz.iterdir():
        if p.is_dir() and p.name.isdigit():
            nums.append(int(p.name))

    n = (max(nums) + 1) if nums else 1
    d = raiz / str(n)
    d.mkdir(parents=True, exist_ok=True)
    return d


def listar_dirs_numerados(carpeta: Path):
    """Lista las carpetas numéricas existentes."""
    if not carpeta.exists():
        return []
    return sorted(
        [p for p in carpeta.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda x: int(x.name)
    )


def borrar_dir_completa(carpeta: Path):
    """Elimina una carpeta y todo su contenido."""
    for p in sorted(carpeta.rglob("*"), reverse=True):
        try:
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
        except Exception as e:
            print(f"[WARN] No pude borrar {p}: {e}")

    try:
        carpeta.rmdir()
    except Exception as e:
        print(f"[WARN] No pude borrar {carpeta}: {e}")


def limitar_dirs_debug(raiz: Path | None, max_dirs: int):
    """Limita el número de carpetas de depuración almacenadas."""
    if raiz is None or not raiz.exists():
        return

    dirs = listar_dirs_numerados(raiz)
    if len(dirs) <= max_dirs:
        return

    for d in dirs[: len(dirs) - max_dirs]:
        borrar_dir_completa(d)


def limitar_imgs_carpeta(carpeta: Path, max_imgs: int):
    """Limita el número de imágenes guardadas en una carpeta."""
    imgs = listar_imgs(carpeta)
    if len(imgs) <= max_imgs:
        return

    imgs = sorted(imgs, key=lambda x: x.stat().st_mtime)
    for p in imgs[: len(imgs) - max_imgs]:
        try:
            p.unlink()
        except Exception as e:
            print(f"[WARN] No pude borrar {p}: {e}")


def borrar_input(path: Path):
    """Elimina la imagen de entrada ya procesada."""
    try:
        path.unlink()
        print(f"[INFO] Imagen input eliminada: {path}")
    except Exception as e:
        print(f"[WARN] No se pudo borrar imagen input {path}: {e}")


# GEOMETRÍA
def order_points(pts: np.ndarray) -> np.ndarray:
    """Ordena cuatro puntos para aplicar la corrección de perspectiva."""
    pts = pts.astype(np.float32)
    rect = np.zeros((4, 2), dtype=np.float32)

    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]

    diff = np.diff(pts, axis=1).reshape(-1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]

    return rect


def polygon_to_corrected_quad(points: np.ndarray) -> np.ndarray:
    """Convierte el polígono detectado en un cuadrilátero corregido."""
    pts = points.astype(np.float32).reshape((-1, 1, 2))
    hull = cv2.convexHull(pts)
    peri = cv2.arcLength(hull, True)

    best_quad = None
    best_area = -1.0

    for eps_frac in np.linspace(0.005, 0.08, 25):
        approx = cv2.approxPolyDP(hull, eps_frac * peri, True)
        if len(approx) == 4:
            quad = approx.reshape(4, 2).astype(np.float32)
            area = cv2.contourArea(quad)
            if area > best_area:
                best_area = area
                best_quad = quad

    if best_quad is not None:
        return order_points(best_quad)

    hull_pts = hull.reshape(-1, 2).astype(np.float32)

    s = hull_pts.sum(axis=1)
    d = np.diff(hull_pts, axis=1).reshape(-1)

    tl = hull_pts[np.argmin(s)]
    br = hull_pts[np.argmax(s)]
    tr = hull_pts[np.argmin(d)]
    bl = hull_pts[np.argmax(d)]

    quad = np.array([tl, tr, br, bl], dtype=np.float32)
    return order_points(quad)


def warp_from_original(image: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Recorta y corrige la perspectiva del panel detectado."""
    rect = order_points(quad)
    (tl, tr, br, bl) = rect

    width_top = np.linalg.norm(tr - tl)
    width_bottom = np.linalg.norm(br - bl)
    height_left = np.linalg.norm(tl - bl)
    height_right = np.linalg.norm(tr - br)

    target_width = int((width_top + width_bottom) / 2)
    target_height = int((height_left + height_right) / 2)

    target_width = max(target_width, 100)
    target_height = max(target_height, 100)

    dst = np.array([
        [0, 0],
        [target_width - 1, 0],
        [target_width - 1, target_height - 1],
        [0, target_height - 1]
    ], dtype=np.float32)

    M = cv2.getPerspectiveTransform(rect, dst)

    warped = cv2.warpPerspective(
        image,
        M,
        (target_width, target_height),
        flags=cv2.INTER_LINEAR
    )

    return warped


def draw_poly(image: np.ndarray, points: np.ndarray, color=(0, 255, 0), thickness=4) -> np.ndarray:
    """Dibuja el contorno de un polígono sobre la imagen."""
    vis = image.copy()
    pts = points.astype(np.int32).reshape((-1, 1, 2))
    cv2.polylines(vis, [pts], True, color, thickness)
    return vis


def draw_points(image: np.ndarray, points: np.ndarray, color=(255, 0, 0), radius=8) -> np.ndarray:
    """Dibuja puntos de referencia sobre la imagen."""
    vis = image.copy()
    for x, y in points.astype(np.int32):
        cv2.circle(vis, (int(x), int(y)), radius, color, -1)
    return vis


# ROBOFLOW
def prediction_score(pred: dict) -> float:
    """Calcula una puntuación para comparar predicciones."""
    conf = float(pred.get("confidence", 0.0))
    width = float(pred.get("width", 0.0))
    height = float(pred.get("height", 0.0))
    area = width * height
    return conf * 1_000_000 + area


def choose_best_prediction(predictions: list[dict]) -> dict:
    """Elige la mejor predicción devuelta por Roboflow."""
    if not predictions:
        raise ValueError("Roboflow no devolvió predicciones.")
    return max(predictions, key=prediction_score)


def extract_points_from_prediction(pred: dict) -> np.ndarray:
    """Extrae los puntos del polígono de la predicción."""
    points = pred.get("points")
    if not points:
        raise ValueError("La predicción no contiene 'points'.")

    pts = np.array([[float(p["x"]), float(p["y"])] for p in points], dtype=np.float32)

    if len(pts) < 4:
        raise ValueError("La predicción tiene menos de 4 puntos.")

    return pts


def infer_roboflow(image_path: Path) -> dict:
    """Envía la imagen al modelo de Roboflow."""
    client = InferenceHTTPClient(
        api_url="https://detect.roboflow.com",
        api_key=API_KEY
    )
    return client.infer(str(image_path), model_id=MODEL_ID)


# MAIN
def main():
    """Ejecuta el proceso completo de detección, recorte y guardado."""
    asegurar_dir(SOURCE_DIR)
    asegurar_dir(OUTPUT_DIR)
    dbg_root = asegurar_dir(DEBUG_DIR)

    limitar_imgs_carpeta(OUTPUT_DIR, MAX_IMGS_OUTPUT)
    limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG)

    ult = ultima_img(SOURCE_DIR)

    if ult is None:
        print("[INFO] No hay imágenes en input.")
        return 0

    print(f"[INFO] Procesando imagen: {ult}")

    img = imread_unicode(ult)
    if img is None:
        print("[ERROR] No se pudo leer la imagen.")
        borrar_input(ult)
        limitar_imgs_carpeta(OUTPUT_DIR, MAX_IMGS_OUTPUT)
        limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG)
        return 1

    dbg = siguiente_dbg(dbg_root)
    guardar_dbg(dbg, "00_original.jpg", img)

    try:
        result = infer_roboflow(ult)
    except Exception as e:
        print(f"[ERROR] Fallo en inferencia Roboflow: {e}")

        if dbg is not None:
            try:
                with open(dbg / "ERROR_ROBOFLOW_info.txt", "w", encoding="utf-8") as f:
                    f.write("Error al llamar a Roboflow.\n")
                    f.write(f"Imagen original: {ult}\n")
                    f.write(f"Error: {e}\n")
                    f.write("La imagen se elimina de input para evitar reprocesamiento infinito.\n")
            except Exception as e2:
                print(f"[WARN] No se pudo guardar TXT debug: {e2}")

        borrar_input(ult)
        limitar_imgs_carpeta(OUTPUT_DIR, MAX_IMGS_OUTPUT)
        limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG)
        return 1

    guardar_json_dbg(dbg, "01_respuesta_roboflow.json", result)

    predictions = result.get("predictions", [])
    if not predictions:
        print("[ERROR] Roboflow no devolvió predicciones.")

        if dbg is not None:
            try:
                with open(dbg / "NO_PANEL_DETECTED_info.txt", "w", encoding="utf-8") as f:
                    f.write("Roboflow no devolvió predicciones.\n")
                    f.write(f"Imagen original: {ult}\n")
                    f.write("La imagen se elimina de input para evitar reprocesamiento infinito.\n")
            except Exception as e:
                print(f"[WARN] No se pudo guardar TXT debug: {e}")

        borrar_input(ult)
        limitar_imgs_carpeta(OUTPUT_DIR, MAX_IMGS_OUTPUT)
        limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG)
        return 1

    best_pred = choose_best_prediction(predictions)
    polygon = extract_points_from_prediction(best_pred)

    preview_points = draw_points(img, polygon, color=(255, 0, 0), radius=8)
    guardar_dbg(dbg, "02_points.jpg", preview_points)

    preview_polygon = draw_poly(img, polygon, color=(0, 255, 0), thickness=4)
    guardar_dbg(dbg, "03_preview_polygon.jpg", preview_polygon)

    quad = polygon_to_corrected_quad(polygon)

    preview_quad_points = draw_points(img, quad, color=(0, 255, 255), radius=10)
    guardar_dbg(dbg, "04_quad_points.jpg", preview_quad_points)

    preview_quad = draw_poly(img, quad, color=(0, 0, 255), thickness=4)
    guardar_dbg(dbg, "05_preview_corrected_quad.jpg", preview_quad)

    preview_both = draw_poly(preview_polygon, quad, color=(0, 0, 255), thickness=4)
    guardar_dbg(dbg, "06_polygon_and_quad.jpg", preview_both)

    warped = warp_from_original(img, quad)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out = OUTPUT_DIR / f"{ts}{ult.suffix.lower()}"

    ok = imwrite_unicode(out, warped)
    if not ok:
        print(f"[ERROR] No se pudo guardar el output: {out}")
        borrar_input(ult)
        limitar_imgs_carpeta(OUTPUT_DIR, MAX_IMGS_OUTPUT)
        limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG)
        return 1

    guardar_dbg(dbg, "07_panel_final.jpg", warped)

    print(f"[OK] Guardado panel: {out}")

    borrar_input(ult)

    limitar_imgs_carpeta(OUTPUT_DIR, MAX_IMGS_OUTPUT)
    limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
