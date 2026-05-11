# -*- coding: utf-8 -*-
"""
Parallel Text Processor - Standalone (FIXED + DEBUG)
- Stable HTML render using QTextDocument + QGraphicsScene (captures shadow reliably)
- Auto-scales label positions using info.txt resolution_slides
- Debug controlled by:
    TEXT_DEBUG=1
    TEXT_DEBUG_HTML=1
"""

import os
import re
import time
import json
import shutil
import tempfile
from pathlib import Path
from multiprocessing import Pool, cpu_count

import cv2
import numpy as np
from PIL import Image

from config import (
    ENABLE_TEXT_SHADOW,
    SHADOW_BLUR_RADIUS,
    SHADOW_COLOR,
    SHADOW_OFFSET_X,
    SHADOW_OFFSET_Y,
)

# =========================
# Debug helpers
# =========================

DEBUG = os.environ.get("TEXT_DEBUG", "0").strip() in ("1", "true", "True", "YES", "yes")
DEBUG_HTML = os.environ.get("TEXT_DEBUG_HTML", "0").strip() in ("1", "true", "True", "YES", "yes")


def _dprint(msg: str):
    if DEBUG:
        print(msg)


def _short(s: str, n: int = 160) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[:n] + "..."


def _html_has_dark_text(html: str) -> bool:
    """
    Detect dark text colors in HTML; used to skip the drop-shadow effect
    on labels where a black shadow would create visible ghosting (e.g. black
    text on a light background — final-page memory card).
    """
    if not html:
        return False
    for hex_color in re.findall(r"color\s*:\s*#([0-9a-fA-F]{3,8})", html):
        h = hex_color
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) >= 6:
            try:
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                if (r + g + b) / 3 < 128:
                    return True
            except ValueError:
                continue
    for m in re.finditer(r"color\s*:\s*rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", html):
        try:
            r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if (r + g + b) / 3 < 128:
                return True
        except ValueError:
            continue
    if re.search(
        r"color\s*:\s*(black|navy|maroon|darkblue|darkred|darkgreen|midnightblue|"
        r"darkslategray|dimgray|#000(?![0-9a-fA-F]))",
        html,
        re.IGNORECASE,
    ):
        return True
    return False


# =========================
# Load info.txt map
# =========================

_RES_MAP_CACHE = None


def _find_info_txt() -> str | None:
    envp = os.environ.get("TEXT_INFO_PATH")
    if envp and os.path.exists(envp):
        return envp

    here = Path(__file__).resolve().parent
    for p in [
        here / "info.txt",
        here.parent / "info.txt",
        here.parent.parent / "info.txt",
        Path.cwd() / "info.txt",
    ]:
        if p.exists():
            return str(p)
    return None


def _load_resolution_map() -> dict[str, tuple[int, int]]:
    global _RES_MAP_CACHE
    if _RES_MAP_CACHE is not None:
        return _RES_MAP_CACHE

    res_map: dict[str, tuple[int, int]] = {}
    info_path = _find_info_txt()
    if not info_path:
        _RES_MAP_CACHE = res_map
        _dprint("[Info] info.txt not found -> no autoscale map")
        return _RES_MAP_CACHE

    try:
        info = json.loads(open(info_path, "r", encoding="utf-8").read())
        for name, w, h in info.get("resolution_slides", []):
            res_map[str(name)] = (int(w), int(h))
        _RES_MAP_CACHE = res_map
        _dprint(f"[Info] Loaded resolution map from: {info_path} ({len(res_map)} slides)")
        return _RES_MAP_CACHE
    except Exception as e:
        _RES_MAP_CACHE = {}
        _dprint(f"[Info] Failed to read info.txt: {e}")
        return _RES_MAP_CACHE


# =========================
# HTML helpers
# =========================

def inject_font_family(html_text: str, font_family: str | None) -> str:
    if not font_family:
        return html_text

    html_text = re.sub(r"font-family:\s*[^;'\"]+[;\"]", "", html_text)
    html_text = re.sub(r"font-family:\s*'[^']+'[;\"]?", "", html_text)
    html_text = re.sub(r'font-family:\s*"[^"]+"[;\"]?', "", html_text)

    def add_font_to_style(match):
        style_content = match.group(1)
        new_style = f"font-family: '{font_family}' !important; " + style_content
        return f'style="{new_style}"'

    html_text = re.sub(r'style="([^"]*)"', add_font_to_style, html_text)

    base_style = f"font-family: '{font_family}' !important;"
    html_text = re.sub(r"<p(\s|>)", f'<p style="{base_style}"\\1', html_text)
    html_text = re.sub(r"<span(\s|>)", f'<span style="{base_style}"\\1', html_text)
    html_text = re.sub(r"<div(\s|>)", f'<div style="{base_style}"\\1', html_text)

    return html_text


def _clamp(v: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo


def scale_font_sizes(html_text: str, global_font: float) -> str:
    if not global_font or global_font == 0:
        return html_text

    gf = _clamp(global_font, 0.1, 10.0)

    def replace_font_size(match):
        original_size = float(match.group(1))
        unit = match.group(2) if match.group(2) else "pt"
        new_size = int(original_size * gf)
        new_size = max(1, new_size)
        return f"font-size:{new_size}{unit}"

    return re.sub(r"font-size:(\d+(?:\.\d+)?)(pt|px)?", replace_font_size, html_text)


def make_waw_transparent(html_text: str) -> str:
    html_text = re.sub(
        r"(<span[^>]*color:\s*#000000[^>]*>)\s*و\s*(</span>)",
        lambda m: m.group(1).replace("color:#000000", "color:transparent") + "و" + m.group(2),
        html_text,
    )
    html_text = re.sub(
        r"(<span[^>]*color:\s*#000(?![0-9a-fA-F])[^>]*>)\s*و\s*(</span>)",
        lambda m: m.group(1).replace("color:#000", "color:transparent") + "و" + m.group(2),
        html_text,
    )
    html_text = re.sub(
        r"(<span[^>]*color:\s*black[^>]*>)\s*و\s*(</span>)",
        lambda m: m.group(1).replace("color:black", "color:transparent") + "و" + m.group(2),
        html_text,
    )
    return html_text


def _scale_rect(x, y, w, h, rx, ry):
    return int(x * rx), int(y * ry), int(w * rx), int(h * ry)


# =========================
# Worker (stable render)
# =========================

def process_single_image_worker(args):
    """
    args:
      (image_name, image_path, text_data_list, is_first_slide, first_font_path, rest_font_path)
    returns:
      (image_name, png_bytes_or_None, status_str)
    """
    (image_name, image_path, text_data_list, is_first_slide, first_font_path, rest_font_path) = args

    try:
        from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QGraphicsScene, QGraphicsTextItem
        from PySide6.QtGui import QPainter, QFontDatabase, QColor, QImage, QTextDocument
        from PySide6.QtCore import Qt, QRectF

        app = QApplication.instance()
        if app is None:
            app = QApplication([])

        # Load fonts
        fonts_loaded = {}
        if first_font_path and os.path.exists(first_font_path):
            fid = QFontDatabase.addApplicationFont(first_font_path)
            if fid != -1:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    fonts_loaded["first"] = fams[0]
        if rest_font_path and os.path.exists(rest_font_path):
            fid = QFontDatabase.addApplicationFont(rest_font_path)
            if fid != -1:
                fams = QFontDatabase.applicationFontFamilies(fid)
                if fams:
                    fonts_loaded["rest"] = fams[0]

        font_family = None
        if is_first_slide and "first" in fonts_loaded:
            font_family = fonts_loaded["first"]
        elif (not is_first_slide) and "rest" in fonts_loaded:
            font_family = fonts_loaded["rest"]

        # Read image
        base_cv = cv2.imread(str(image_path))
        if base_cv is None:
            return (image_name, None, "Failed to load image")

        base_h, base_w = base_cv.shape[:2]

        # Scale from design res (info.txt)
        res_map = _load_resolution_map()
        design_w, design_h = res_map.get(image_name, (base_w, base_h))
        rx =  1.0
        ry =  1.0

        if DEBUG:
            _dprint("=" * 80)
            _dprint(f"[Worker] {image_name} base={base_w}x{base_h} design={design_w}x{design_h} scale=({rx:.4f},{ry:.4f})")
            _dprint(f"[Worker] font_family={font_family} shadow={ENABLE_TEXT_SHADOW}")

        # Base to QImage
        rgb = cv2.cvtColor(base_cv, cv2.COLOR_BGR2RGB)
        base_q = QImage(rgb.data, base_w, base_h, 3 * base_w, QImage.Format_RGB888)

        out_img = QImage(base_w, base_h, QImage.Format_ARGB32_Premultiplied)
        out_img.fill(Qt.transparent)

        painter = QPainter(out_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.drawImage(0, 0, base_q)

        def render_label(html, w, h):
            doc = QTextDocument()
            doc.setHtml(html)
            doc.setTextWidth(max(1, int(w)))

            item = QGraphicsTextItem()
            item.setDocument(doc)

            if ENABLE_TEXT_SHADOW and not _html_has_dark_text(html):
                eff = QGraphicsDropShadowEffect()
                eff.setBlurRadius(int(SHADOW_BLUR_RADIUS))
                eff.setColor(QColor(*SHADOW_COLOR))
                eff.setOffset(int(SHADOW_OFFSET_X), int(SHADOW_OFFSET_Y))
                item.setGraphicsEffect(eff)

            scene = QGraphicsScene()
            scene.addItem(item)

            img = QImage(int(w), int(h), QImage.Format_ARGB32_Premultiplied)
            img.fill(Qt.transparent)

            p = QPainter(img)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.TextAntialiasing, True)
            scene.render(p, QRectF(0, 0, w, h), QRectF(0, 0, w, h))
            p.end()
            return img

        for idx, element in enumerate(text_data_list, 1):
            html = element.get("html", "") or ""
            x = int(element.get("x", 0) or 0)
            y = int(element.get("y", 0) or 0)
            w = int(element.get("width", 400) or 400)
            h = int(element.get("height", 200) or 200)
            gf = float(element.get("global_font", 0) or 0)

            sx, sy, sw, sh = _scale_rect(x, y, w, h, rx, ry)

            if font_family:
                html = inject_font_family(html, font_family)
            if gf != 0:
                html = scale_font_sizes(html, gf)
            html = make_waw_transparent(html)

            if DEBUG:
                _dprint("-" * 70)
                _dprint(f"[Worker] Label {idx} rect_design=({x},{y},{w},{h}) gf={gf}")
                _dprint(f"[Worker] Label {idx} rect_scaled=({sx},{sy},{sw},{sh})")
                _dprint(f"[Worker] html_preview={_short(html)}")
                if DEBUG_HTML:
                    _dprint(html)

            label_img = render_label(html, max(1, sw), max(1, sh))
            painter.drawImage(int(sx), int(sy), label_img)

        painter.end()

        # QImage -> PNG bytes
        # Use Qt save to temp bytes by encoding through OpenCV after extracting bytes safely:
        out_img = out_img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        w2, h2 = out_img.width(), out_img.height()
        bpl = out_img.bytesPerLine()
        raw = out_img.bits().tobytes()
        arr = np.frombuffer(raw, dtype=np.uint8).reshape((h2, bpl // 4, 4))
        arr = arr[:, :w2, :]
        bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        ok, png = cv2.imencode(".png", bgr)
        if not ok:
            return (image_name, None, "Failed to encode output PNG")

        return (image_name, png.tobytes(), "OK")

    except Exception as e:
        return (image_name, None, f"Worker error: {e}")


# =========================
# Main Parallel API
# =========================

def apply_text_parallel(images_dict: dict,
                        text_data: dict,
                        first_font_path: str,
                        rest_font_path: str,
                        num_workers: int | None = None) -> dict:
    if num_workers is None:
        num_workers = max(1, cpu_count() - 1)

    print(f"\n[Parallel] Starting with {num_workers} workers")

    temp_dir = tempfile.mkdtemp(prefix="parallel_text_")
    temp_path = Path(temp_dir)

    try:
        tasks = []
        for idx, (image_name, img) in enumerate(images_dict.items()):
            if image_name not in text_data:
                continue

            labels_list = text_data[image_name]
            is_first = (idx == 0)

            tmp_file = temp_path / f"{image_name}.png"
            cv2.imwrite(str(tmp_file), img)

            tasks.append((
                image_name,
                str(tmp_file),
                labels_list,
                is_first,
                first_font_path,
                rest_font_path
            ))

        if not tasks:
            print("[Parallel] No tasks found. Returning original images.")
            return images_dict

        print(f"[Parallel] Prepared {len(tasks)} tasks")
        print("[Parallel] Processing...")

        start = time.time()

        with Pool(processes=num_workers) as pool:
            results = pool.map(process_single_image_worker, tasks)

        processed_images = {}
        failed = 0

        for i, (image_name, image_bytes, status) in enumerate(results, 1):
            if image_bytes is not None:
                nparr = np.frombuffer(image_bytes, np.uint8)
                out = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if out is not None:
                    processed_images[image_name] = out
                    print(f"[{i}/{len(tasks)}] OK  {image_name}")
                else:
                    failed += 1
                    print(f"[{i}/{len(tasks)}] FAIL {image_name} - decode failed")
                    if image_name in images_dict:
                        processed_images[image_name] = images_dict[image_name]
            else:
                failed += 1
                print(f"[{i}/{len(tasks)}] FAIL {image_name} - {status}")
                if image_name in images_dict:
                    processed_images[image_name] = images_dict[image_name]

        # Add untouched images
        for image_name, img in images_dict.items():
            if image_name not in processed_images:
                processed_images[image_name] = img

        elapsed = time.time() - start
        ok_count = len(tasks) - failed

        print("\n" + "=" * 60)
        print("[Parallel] Done")
        print(f"[Parallel] Success: {ok_count}/{len(tasks)}")
        if failed:
            print(f"[Parallel] Failed:  {failed}/{len(tasks)}")
        print(f"[Parallel] Time:    {elapsed:.2f}s")
        if elapsed > 0:
            print(f"[Parallel] Speed:   {len(tasks)/elapsed:.2f} img/s")
        print("=" * 60 + "\n")

        return processed_images

    finally:
        try:
            shutil.rmtree(temp_dir)
        except Exception as e:
            print(f"[Parallel] Warning: failed to remove temp dir: {e}")


# =========================
# PDF helper
# =========================

def create_pdf_from_images(images_list: list, output_path: str) -> bool:
    if not images_list:
        print("[PDF] No images provided.")
        return False

    print("[PDF] Creating PDF...")

    pil_images = []
    for idx, img in enumerate(images_list, 1):
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)

        if pil_img.mode == "RGBA":
            rgb_img = Image.new("RGB", pil_img.size, (255, 255, 255))
            rgb_img.paste(pil_img, mask=pil_img.split()[3])
            pil_images.append(rgb_img)
        else:
            pil_images.append(pil_img.convert("RGB"))

        print(f"[PDF] Converting {idx}/{len(images_list)}")

    try:
        first = pil_images[0]
        rest = pil_images[1:] if len(pil_images) > 1 else []
        first.save(
            output_path,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=rest if rest else None,
        )
        print(f"[PDF] Done: {output_path}")
        return True

    except Exception as e:
        print(f"[PDF] Failed: {e}")
        return False


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Parallel Text Processor - Standalone")
    print("=" * 60 + "\n")
    print("Import and use:")
    print("  from parallel_text_processor import apply_text_parallel")