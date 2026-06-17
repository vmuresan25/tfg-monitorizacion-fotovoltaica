from __future__ import annotations
from pathlib import Path

import cv2
import numpy as np


#CONFIG
INPUT_DIR = Path("output")
RESULT_DIR = Path("result")
DEBUG_DIR = Path("debug_result")

EXTS_IMG = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

BORRAR_ORIGINAL = True

MAX_IMGS_RESULT = 200
MAX_DIRS_DEBUG_RESULT = 60

UMBRAL_SUCIEDAD_PCT = 2.0

# Objetos
AREA_MIN_COMP_PX = 80

#Sombra
AREA_MIN_SOMBRA_FRAC = 0.015

# Supresión de rejilla
SUPRIMIR_REJILLA = True
REJILLA_DILATE = 2
REJILLA_LEN_RATIO = 0.06

# Detección de objetos
VERDE_HSV_LO = (28, 35, 35)
VERDE_HSV_HI = (95, 255, 255)

# Umbrales afinados
ANOM_V_DIFF = 65   # Subido un poco para ignorar brillos metálicos menores
ANOM_AB_DIFF = 18  # Más estricto con el cambio de color

PIEL_YCRCB_LO = (0, 133, 77)
PIEL_YCRCB_HI = (255, 173, 127)


#IO
def asegurar_dir(ruta: Path) -> Path:
    """Crea una carpeta si no existe y devuelve su ruta."""
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def listar_imgs(carpeta: Path) -> list[Path]:
    """Devuelve la lista de imágenes válidas encontradas en una carpeta."""
    if not carpeta.exists():
        return []
    imgs: list[Path] = []
    for p in carpeta.rglob("*"):
        if p.is_file() and p.suffix.lower() in EXTS_IMG:
            imgs.append(p)
    return sorted(imgs)


def ultima_img(carpeta: Path) -> Path | None:
    """Obtiene la imagen más reciente de una carpeta."""
    imgs = listar_imgs(carpeta)
    return max(imgs, key=lambda x: x.stat().st_mtime) if imgs else None


def guardar_dbg(dbg: Path | None, nombre: str, img: np.ndarray) -> None:
    """Guarda una imagen auxiliar de depuración si existe una carpeta debug."""
    if dbg is not None:
        cv2.imwrite(str(dbg / nombre), img)


def siguiente_dbg(raiz: Path | None) -> Path | None:
    """Crea una nueva carpeta numerada para guardar los archivos de depuración."""
    if raiz is None:
        return None
    nums: list[int] = []
    if raiz.exists():
        for p in raiz.iterdir():
            if p.is_dir() and p.name.isdigit():
                nums.append(int(p.name))
    n = (max(nums) + 1) if nums else 1
    d = raiz / str(n)
    d.mkdir(parents=True, exist_ok=True)
    return d


def listar_dirs_numerados(carpeta: Path) -> list[Path]:
    """Lista las carpetas numeradas existentes dentro de una carpeta."""
    if not carpeta.exists():
        return []
    return sorted(
        [p for p in carpeta.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda p: int(p.name)
    )


def borrar_dir_completa(carpeta: Path) -> None:
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


def limitar_dirs_debug(raiz: Path | None, max_dirs: int) -> None:
    """Limita el número de carpetas de depuración almacenadas."""
    if raiz is None or not raiz.exists():
        return

    dirs = listar_dirs_numerados(raiz)

    if len(dirs) <= max_dirs:
        return

    for d in dirs[:len(dirs) - max_dirs]:
        borrar_dir_completa(d)


def limitar_imgs_carpeta(carpeta: Path, max_imgs: int) -> None:
    """Limita el número de imágenes guardadas en una carpeta."""
    imgs = listar_imgs(carpeta)

    if len(imgs) <= max_imgs:
        return

    imgs = sorted(imgs, key=lambda p: p.stat().st_mtime)

    for p in imgs[:len(imgs) - max_imgs]:
        try:
            p.unlink()
        except Exception as e:
            print(f"[WARN] No pude borrar {p}: {e}")


#MORFOLOGÍA / MÁSCARAS
def limpiar_mascara(mask: np.ndarray, k: int = 3, it_open: int = 1, it_close: int = 1) -> np.ndarray:
    """Limpia una máscara aplicando operaciones morfológicas de apertura y cierre."""
    k = max(1, int(k))
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    out = mask.copy()
    if it_open > 0:
        out = cv2.morphologyEx(out, cv2.MORPH_OPEN, ker, iterations=int(it_open))
    if it_close > 0:
        out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, ker, iterations=int(it_close))
    return out


def filtrar_componentes_inteligente(mask: np.ndarray, area_min_px: int) -> np.ndarray:
    """Filtra los componentes de una máscara según su área y forma."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = np.zeros_like(mask)

    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]

        aspect_ratio = max(w, h) / (min(w, h) + 1e-5)

        if area >= area_min_px and aspect_ratio < 5.0:
            keep[labels == i] = 255
    return keep


def mascara_a_cajas(mask: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Convierte las regiones detectadas de una máscara en cajas delimitadoras."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cajas: list[tuple[int, int, int, int]] = []
    for c in cnts:
        x, y, w, h = cv2.boundingRect(c)
        cajas.append((x, y, w, h))
    return cajas


#REJILLA
def construir_mascara_rejilla(img_bgr: np.ndarray) -> np.ndarray:
    """Construye una máscara para detectar y suprimir líneas de la rejilla del panel."""
    h, w = img_bgr.shape[:2]
    gris = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    kernel_th = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))
    tophat = cv2.morphologyEx(gris, cv2.MORPH_TOPHAT, kernel_th)

    _, thr = cv2.threshold(tophat, 40, 255, cv2.THRESH_BINARY)

    klen_h = max(15, int(w * float(REJILLA_LEN_RATIO)))
    klen_v = max(15, int(h * float(REJILLA_LEN_RATIO)))

    kh = cv2.getStructuringElement(cv2.MORPH_RECT, (klen_h, 1))
    kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, klen_v))

    lineas_h = cv2.morphologyEx(thr, cv2.MORPH_OPEN, kh)
    lineas_v = cv2.morphologyEx(thr, cv2.MORPH_OPEN, kv)

    rejilla = cv2.bitwise_or(lineas_h, lineas_v)
    rejilla = cv2.morphologyEx(rejilla, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))

    if int(REJILLA_DILATE) > 0:
        kd = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (int(REJILLA_DILATE) * 2 + 1, int(REJILLA_DILATE) * 2 + 1)
        )
        rejilla = cv2.dilate(rejilla, kd, iterations=1)

    return rejilla


#OBJETOS
def mascara_objetos(img_bgr: np.ndarray) -> np.ndarray:
    """Genera una máscara de posibles objetos o anomalías visibles sobre el panel."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    verde = cv2.inRange(hsv, np.array(VERDE_HSV_LO, np.uint8), np.array(VERDE_HSV_HI, np.uint8))
    verde = limpiar_mascara(verde, k=5, it_open=1, it_close=2)

    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)

    V = hsv[:, :, 2].astype(np.float32)
    A = lab[:, :, 1].astype(np.float32)
    B = lab[:, :, 2].astype(np.float32)

    V_suav = cv2.GaussianBlur(V, (0, 0), 21.0)
    A_suav = cv2.GaussianBlur(A, (0, 0), 21.0)
    B_suav = cv2.GaussianBlur(B, (0, 0), 21.0)

    dV = np.abs(V - V_suav)
    dab = (np.abs(A - A_suav) + np.abs(B - B_suav)) / 2.0

    anom_color = (dab >= int(ANOM_AB_DIFF)).astype(np.uint8) * 255
    anom_contraste = (dV >= int(ANOM_V_DIFF)).astype(np.uint8) * 255

    anom = cv2.bitwise_or(anom_color, anom_contraste)
    anom = limpiar_mascara(anom, k=3, it_open=1, it_close=2)

    ycrcb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2YCrCb)
    piel = cv2.inRange(ycrcb, np.array(PIEL_YCRCB_LO, np.uint8), np.array(PIEL_YCRCB_HI, np.uint8))
    piel = limpiar_mascara(piel, k=5, it_open=1, it_close=2)

    base = cv2.bitwise_or(verde, anom)
    base = cv2.bitwise_or(base, piel)

    h, w = base.shape
    base[0:10, :] = 0
    base[h-10:h, :] = 0
    base[:, 0:10] = 0
    base[:, w-10:w] = 0

    return base


#SOMBRAS
def mascara_sombra_anclaje(
    img_bgr: np.ndarray,
    mask_obj: np.ndarray,
    mask_rejilla: np.ndarray,
) -> tuple[np.ndarray, dict]:
    """Detecta zonas de sombra usando una referencia de luminosidad del propio panel."""
    hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
    V = hsv[:, :, 2].astype(np.float32)

    kd_obj = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (85, 85))
    obj_dil = cv2.dilate(mask_obj, kd_obj)

    ignorar = cv2.bitwise_or(mask_rejilla, obj_dil)
    mascara_valida = cv2.bitwise_not(ignorar)

    v_validos = V[mascara_valida == 255]
    if v_validos.size < 1000:
        return np.zeros_like(mask_obj), {"motivo": "pocos_pixeles_validos", "validos": int(v_validos.size)}

    ref_v = float(np.percentile(v_validos, 75))
    p25 = float(np.percentile(v_validos, 25))
    caida_dyn = float(np.clip(ref_v - p25, 10.0, 60.0))
    thr_v = ref_v - caida_dyn

    sombra = (V < thr_v).astype(np.uint8) * 255
    sombra = cv2.bitwise_and(sombra, mascara_valida)
    sombra = limpiar_mascara(sombra, k=7, it_open=1, it_close=3)

    return sombra, {"ref_v": ref_v, "thr_v": thr_v}


#PIPELINE
def detectar_objetos_y_sombra(img_bgr: np.ndarray, dbg: Path | None = None) -> dict:
    """Ejecuta el proceso completo de detección de objetos, sombras y suciedad."""
    mask_rejilla = construir_mascara_rejilla(img_bgr)
    guardar_dbg(dbg, "01_mask_rejilla.jpg", mask_rejilla)

    base_obj = mascara_objetos(img_bgr)
    guardar_dbg(dbg, "02_mask_base_objetos.jpg", base_obj)

    if SUPRIMIR_REJILLA:
        mask_obj_sin_rejilla = cv2.bitwise_and(base_obj, cv2.bitwise_not(mask_rejilla))
        guardar_dbg(dbg, "03_mask_objetos_sin_rejilla.jpg", mask_obj_sin_rejilla)

        k_heal = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask_obj_close = cv2.morphologyEx(mask_obj_sin_rejilla, cv2.MORPH_CLOSE, k_heal)
        guardar_dbg(dbg, "04_mask_objetos_close.jpg", mask_obj_close)
    else:
        mask_obj_close = base_obj

    mask_obj = filtrar_componentes_inteligente(mask_obj_close, AREA_MIN_COMP_PX)
    guardar_dbg(dbg, "05_mask_objetos_filtrada.jpg", mask_obj)

    sombra_cruda, sombra_info = mascara_sombra_anclaje(img_bgr, mask_obj, mask_rejilla)
    guardar_dbg(dbg, "06_mask_sombra_cruda.jpg", sombra_cruda)

    k_bridge = cv2.getStructuringElement(cv2.MORPH_RECT, (45, 45))
    sombra_unida = cv2.morphologyEx(sombra_cruda, cv2.MORPH_CLOSE, k_bridge)
    guardar_dbg(dbg, "07_mask_sombra_unida.jpg", sombra_unida)

    h_img, w_img = img_bgr.shape[:2]
    area_min_shd = int(h_img * w_img * AREA_MIN_SOMBRA_FRAC)
    sombra_grande = filtrar_componentes_inteligente(sombra_unida, area_min_shd)
    guardar_dbg(dbg, "08_mask_sombra_grande.jpg", sombra_grande)

    sombra_final = cv2.bitwise_and(sombra_cruda, sombra_grande)
    guardar_dbg(dbg, "09_mask_sombra_final_antes_rejilla.jpg", sombra_final)

    if SUPRIMIR_REJILLA:
        mask_sombra = cv2.bitwise_and(sombra_final, cv2.bitwise_not(mask_rejilla))
    else:
        mask_sombra = sombra_final

    guardar_dbg(dbg, "10_mask_sombra_final.jpg", mask_sombra)

    mask_obj_sin_sombra = cv2.bitwise_and(mask_obj, cv2.bitwise_not(mask_sombra))
    guardar_dbg(dbg, "11_mask_objetos_sin_sombra.jpg", mask_obj_sin_sombra)

    mask_obj = filtrar_componentes_inteligente(mask_obj_sin_sombra, AREA_MIN_COMP_PX)
    guardar_dbg(dbg, "12_mask_objetos_final.jpg", mask_obj)

    overlay_obj = img_bgr.copy()
    overlay_obj[mask_obj > 0] = (0, 255, 255)
    guardar_dbg(dbg, "13_overlay_objetos.jpg", overlay_obj)

    overlay_sombra = img_bgr.copy()
    overlay_sombra[mask_sombra > 0] = (255, 255, 0)
    guardar_dbg(dbg, "14_overlay_sombras.jpg", overlay_sombra)

    overlay_total = img_bgr.copy()
    overlay_total[mask_obj > 0] = (0, 255, 255)
    overlay_total[mask_sombra > 0] = (255, 255, 0)
    guardar_dbg(dbg, "15_overlay_total.jpg", overlay_total)

    area_obj = float(cv2.countNonZero(mask_obj))
    area_sombra = float(cv2.countNonZero(mask_sombra))
    area_total = float(img_bgr.shape[0] * img_bgr.shape[1])

    suciedad_pct = ((area_obj + area_sombra) / area_total) * 100.0
    sucio = "SI" if suciedad_pct >= UMBRAL_SUCIEDAD_PCT else "NO"

    tipo = "SIN_REGION"
    if area_obj > 0 and area_sombra > 0:
        tipo = "MIXTO"
    elif area_obj > 0:
        tipo = "OBJETO"
    elif area_sombra > 0:
        tipo = "SOMBRA"

    if dbg is not None:
        with open(dbg / "16_metricas.txt", "w", encoding="utf-8") as f:
            f.write(f"area_obj={area_obj}\n")
            f.write(f"area_sombra={area_sombra}\n")
            f.write(f"area_total={area_total}\n")
            f.write(f"suciedad_pct={suciedad_pct:.6f}\n")
            f.write(f"sucio={sucio}\n")
            f.write(f"tipo={tipo}\n")
            f.write(f"umbral_suciedad_pct={UMBRAL_SUCIEDAD_PCT}\n")
            f.write(f"area_min_comp_px={AREA_MIN_COMP_PX}\n")
            f.write(f"area_min_sombra_frac={AREA_MIN_SOMBRA_FRAC}\n")
            f.write(f"suprimir_rejilla={SUPRIMIR_REJILLA}\n")
            f.write(f"sombra_info={sombra_info}\n")

    return {
        "mask_obj": mask_obj,
        "mask_sombra": mask_sombra,
        "suciedad_pct": suciedad_pct,
        "sucio": sucio,
        "tipo": tipo,
    }


def dibujar_salida(img_bgr: np.ndarray, info: dict) -> np.ndarray:
    """Dibuja sobre la imagen final las regiones detectadas y las métricas obtenidas."""
    out = img_bgr.copy()
    color_obj = (0, 255, 255)
    color_sombra = (255, 255, 0)

    for (x, y, w, h) in mascara_a_cajas(info["mask_obj"]):
        cv2.rectangle(out, (x, y), (x + w, y + h), color_obj, 2)
        cv2.putText(out, "OBJ", (x, max(0, y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_obj, 2)

    cnts, _ = cv2.findContours(info["mask_sombra"], cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if cnts:
        min_x = min(cv2.boundingRect(c)[0] for c in cnts)
        min_y = min(cv2.boundingRect(c)[1] for c in cnts)
        max_x = max(cv2.boundingRect(c)[0] + cv2.boundingRect(c)[2] for c in cnts)
        max_y = max(cv2.boundingRect(c)[1] + cv2.boundingRect(c)[3] for c in cnts)
        cv2.rectangle(out, (min_x, min_y), (max_x, max_y), color_sombra, 2)
        cv2.putText(out, "SHD", (min_x, max(0, min_y - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_sombra, 2)

    cv2.putText(out, f"Suciedad: {info['suciedad_pct']:.2f}%", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 0), 3)
    cv2.putText(out, f"Sucio: {info['sucio']}", (20, 95), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 0), 3)
    cv2.putText(out, f"Tipo: {info['tipo']}", (20, 140), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 0), 3)

    return out


def main() -> None:
    """Ejecuta el procesamiento de la última imagen disponible y guarda el resultado final."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    dbg_root = asegurar_dir(DEBUG_DIR)

    limitar_imgs_carpeta(RESULT_DIR, MAX_IMGS_RESULT)
    limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG_RESULT)

    ruta = ultima_img(INPUT_DIR)
    if ruta is None:
        return

    dbg_run = siguiente_dbg(dbg_root)

    img = cv2.imread(str(ruta), cv2.IMREAD_COLOR)
    if img is None:
        return

    guardar_dbg(dbg_run, "00_panel_entrada.jpg", img)

    info = detectar_objetos_y_sombra(img, dbg_run)
    salida = dibujar_salida(img, info)

    run_id = int(dbg_run.name) if dbg_run and dbg_run.name.isdigit() else 0

    guardar_dbg(dbg_run, "17_resultado_anotado.jpg", salida)

    cv2.imwrite(str(RESULT_DIR / f"{run_id:04d}_{info['tipo']}.jpg"), salida)

    if BORRAR_ORIGINAL:
        ruta.unlink()

    limitar_imgs_carpeta(RESULT_DIR, MAX_IMGS_RESULT)
    limitar_dirs_debug(dbg_root, MAX_DIRS_DEBUG_RESULT)

    print(f"Procesado -> ID {run_id:04d} -> {info['tipo']} ({info['sucio']}). Suciedad: {info['suciedad_pct']:.2f}%")


if __name__ == "__main__":
    main()

