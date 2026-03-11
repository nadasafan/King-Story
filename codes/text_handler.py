# -*- coding: utf-8 -*-
"""
Text Handler Module (FINAL - API SAFE)

✅ Fixes for FastAPI/uvicorn:
- Forces Qt to run OFFSCREEN (no GUI) BEFORE importing PySide6
- Ensures QApplication exists BEFORE using QFontDatabase
- Uses a global lock because Qt painting is NOT thread-safe (prevents random hangs/crashes)
- Stable HTML render using QTextDocument + QGraphicsScene (shadow supported)

Debug env vars:
    TEXT_DEBUG=1
    TEXT_DEBUG_HTML=1
Optional:
    TEXT_INFO_PATH=/abs/path/to/info.txt   (for resolution_slides map)
"""
import os
import json

def load_info_file(path="info.txt") -> dict:
    """
    تقرأ ملف info.txt وتعيد البيانات كقاموس.
    لو الملف مش موجود أو فيه مشكلة، ترجع dict فاضي.
    """
    if not os.path.exists(path):
        print(f"[Info] info.txt not found: {path}")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        print(f"[Info] Failed to read info.txt: {e}")
        return {}
import os
import json
import re
import threading
import copy
# from pathlib import Path

import cv2
import numpy as np

# =========================
# MUST be set BEFORE PySide6 import
# =========================
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
# optional: reduce warnings
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.qpa.*=false")

from PySide6.QtWidgets import (
    QApplication,
    QGraphicsDropShadowEffect,
    QGraphicsScene,
    QGraphicsTextItem,
)
from PySide6.QtGui import (
    QFontDatabase,
    QColor,
    QImage,
    QPainter,
    QTextDocument,
)
from PySide6.QtCore import Qt, QRectF

from config import (
    EN_FIRST_SLIDE_FONT, EN_REST_SLIDES_FONT,
    AR_FIRST_SLIDE_FONT, AR_REST_SLIDES_FONT,
    ENABLE_TEXT_SHADOW,
    SHADOW_BLUR_RADIUS, SHADOW_COLOR, SHADOW_OFFSET_X, SHADOW_OFFSET_Y,
)

# =========================
# Global lock (Qt is not thread-safe)
# =========================
_QT_LOCK = threading.Lock()

# =========================
# Debug helpers
# =========================
DEBUG = os.environ.get("TEXT_DEBUG", "0").strip().lower() in ("1", "true", "yes", "y")
DEBUG_HTML = os.environ.get("TEXT_DEBUG_HTML", "0").strip().lower() in ("1", "true", "yes", "y")


def _dprint(msg: str):
    if DEBUG:
        print(msg)


def _short(s: str, n: int = 180) -> str:
    s = (s or "").replace("\n", " ").replace("\r", " ").strip()
    return s if len(s) <= n else s[:n] + "..."


# =========================
# Qt App
# =========================
def _ensure_qt_app():
    """
    MUST be called before any QFontDatabase usage.
    In server mode, we create a single QApplication instance.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# =========================
# Auto-load design resolutions from info.txt
# =========================



# def _find_info_txt() -> str | None:
#     # 1) ENV override
#     envp = os.environ.get("TEXT_INFO_PATH")
#     if envp and os.path.exists(envp):
#         return envp

#     # 2) Try next to this file
#     here = Path(__file__).resolve().parent
#     p1 = here / "info.txt"
#     if p1.exists():
#         return str(p1)

#     # 3) Try project root (one/two levels up)
#     for up in [here.parent, here.parent.parent, Path.cwd()]:
#         p = up / "info.txt"
#         if p.exists():
#             return str(p)

#     return None


# def _load_resolution_map() -> dict[str, tuple[int, int]]:
    
#     res_map: dict[str, tuple[int, int]] = {}
#     info_path = _find_info_txt()
    
#     if not info_path:
        
#         _dprint("[Info] info.txt not found -> no autoscale map")
#         return res_map

#     try:
#         info = json.loads(open(info_path, "r", encoding="utf-8").read())
#         for name, w, h in info.get("resolution_slides", []):
            
#             res_map[str(name)] = (int(w), int(h))

#         _dprint(f"[Info] Loaded resolution map from: {info_path} ({len(res_map)} slides)")
#         return res_map
#     except Exception as e:
        
#         _dprint(f"[Info] Failed to read info.txt: {e}")
#         return {}


# =========================
# Fonts
# =========================
def load_custom_fonts(
    language: str,
    first_slide_font_path: str | None = None,
    rest_slides_font_path: str | None = None,
    base_dir: str | None = None
) -> dict:
    """
    Returns: {"first": "FamilyName", "rest": "FamilyName"}
    IMPORTANT: Ensures QApplication exists before QFontDatabase.
    """
    with _QT_LOCK:
        _ensure_qt_app()

        fonts_loaded: dict = {}

        if first_slide_font_path and base_dir:
            first_font = os.path.join(base_dir, first_slide_font_path)
        elif language == "en":
            first_font = EN_FIRST_SLIDE_FONT
        else:
            first_font = AR_FIRST_SLIDE_FONT

        if rest_slides_font_path and base_dir:
            rest_font = os.path.join(base_dir, rest_slides_font_path)
        elif language == "en":
            rest_font = EN_REST_SLIDES_FONT
        else:
            rest_font = AR_REST_SLIDES_FONT

        # First slide font
        if first_font and os.path.exists(first_font):
            font_id = QFontDatabase.addApplicationFont(first_font)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    fonts_loaded["first"] = families[0]
                    _dprint(f"[Fonts] Loaded FIRST: {families[0]} ({os.path.basename(first_font)})")
            else:
                _dprint(f"[Fonts] Failed to load FIRST font: {first_font}")
        else:
            _dprint(f"[Fonts] FIRST font not found: {first_font}")

        # Rest slides font
        if rest_font and os.path.exists(rest_font):
            font_id = QFontDatabase.addApplicationFont(rest_font)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    fonts_loaded["rest"] = families[0]
                    _dprint(f"[Fonts] Loaded REST:  {families[0]} ({os.path.basename(rest_font)})")
            else:
                _dprint(f"[Fonts] Failed to load REST font: {rest_font}")
        else:
            _dprint(f"[Fonts] REST font not found: {rest_font}")

        return fonts_loaded




def get_slide_fonts(info: dict, slide_name: str, language: str):
    slide_fonts = info.get("fonts", {}).get(slide_name, {})

    first_font = slide_fonts.get("first")
    rest_font  = slide_fonts.get("rest")

    # fallback to config
    if not first_font:
        first_font = EN_FIRST_SLIDE_FONT if language == "en" else AR_FIRST_SLIDE_FONT

    if not rest_font:
        rest_font = EN_REST_SLIDES_FONT if language == "en" else AR_REST_SLIDES_FONT

    return first_font, rest_font
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

    boost = 1.0
    min_pt = 0
    min_px = 0

    def repl(match):
        original_size = float(match.group(1))
        unit = match.group(2) if match.group(2) else "pt"

        new_size = int(original_size * gf * boost)

        if unit == "pt" and min_pt > 0:
            new_size = max(min_pt, new_size)
        elif unit != "pt" and min_px > 0:
            new_size = max(min_px, new_size)

        return f"font-size:{new_size}{unit}"

    return re.sub(r"font-size:(\d+(?:\.\d+)?)(pt|px)?", repl, html_text)


def make_waw_transparent(html_text: str) -> str:
    html_text = re.sub(
        r"(<span[^>]*color:\s*#000000[^>]*>)\s*و\s*(</span>)",
        lambda m: m.group(1).replace("color:#000000", "color:transparent") + "و" + m.group(2),
        html_text
    )
    html_text = re.sub(
        r"(<span[^>]*color:\s*#000(?![0-9a-fA-F])[^>]*>)\s*و\s*(</span>)",
        lambda m: m.group(1).replace("color:#000", "color:transparent") + "و" + m.group(2),
        html_text
    )
    html_text = re.sub(
        r"(<span[^>]*color:\s*black[^>]*>)\s*و\s*(</span>)",
        lambda m: m.group(1).replace("color:black", "color:transparent") + "و" + m.group(2),
        html_text
    )
    return html_text


def replace_name_in_html(html_text: str, user_name: str, is_first_slide: bool = False, language: str = "en") -> str:
    if not user_name:
        return html_text

    repl = user_name.upper() if is_first_slide else user_name

    if language == "en":
        html_text = html_text.replace("[*NAME*]", repl)
        html_text = html_text.replace("[*Name*]", repl)
    elif language == "ar":
        html_text = html_text.replace("[*الاسم*]", repl)

    return html_text


# =========================
# JSON text reader
# =========================
def read_text_data(file_path: str, user_name: str = "", language: str = "en") -> dict | None:
    if not os.path.exists(file_path):
        print(f"[Text] File not found: {file_path}")
        return None

    try:
        raw_content = open(file_path, "r", encoding="utf-8").read()
        if not raw_content.strip():
            return None

        # keep your "clean broken quotes inside html" logic
        result = []
        i = 0
        while i < len(raw_content):
            if raw_content[i:i + 7] == '"html":':
                result.append(raw_content[i:i + 7])
                i += 7

                while i < len(raw_content) and raw_content[i] in " \t":
                    result.append(raw_content[i])
                    i += 1

                if i < len(raw_content) and raw_content[i] == '"':
                    result.append('"')
                    i += 1

                    html_chars = []
                    while i < len(raw_content):
                        ch = raw_content[i]

                        if ch == '"':
                            peek = raw_content[i + 1:i + 20].lstrip()
                            if peek.startswith(",") or peek.startswith("}"):
                                cleaned_html = "".join(html_chars)
                                cleaned_html = cleaned_html.replace('\\"', "'").replace("\\'", "'")
                                cleaned_html = re.sub(r'(?<!=)"(?![>\s])', "'", cleaned_html)
                                cleaned_html = cleaned_html.replace("\\n", " ").replace("\\t", " ").replace("\\r", "")
                                cleaned_html = cleaned_html.replace("\\/", "/")
                                cleaned_html = cleaned_html.replace(',"', ",'").replace('",', "',")
                                result.append(cleaned_html)
                                result.append('"')
                                i += 1
                                break
                            else:
                                html_chars.append("'")
                                i += 1

                        elif ch == "\\" and i + 1 < len(raw_content):
                            nxt = raw_content[i + 1]
                            if nxt in ['"', "'"]:
                                html_chars.append("'")
                                i += 2
                            elif nxt == "\\":
                                html_chars.append("\\")
                                i += 2
                            elif nxt in "ntr":
                                html_chars.append(" ")
                                i += 2
                            else:
                                i += 1
                        else:
                            html_chars.append(ch)
                            i += 1
                    continue

            result.append(raw_content[i])
            i += 1

        content = "".join(result)
        data = copy.deepcopy(json.loads(content))

        # replace name placeholders
        if user_name:
            slide_index = 0
            for image_name, labels_list in data.items():
                if isinstance(labels_list, list):
                    for label in labels_list:
                        if isinstance(label, dict) and "html" in label:
                            label["html"] = replace_name_in_html(
                                label["html"], user_name,
                                is_first_slide=(slide_index == 0),
                                language=language
                            )
                slide_index += 1

        return data

    except json.JSONDecodeError as e:
        print(f"[Text] JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"[Text] Error reading text data: {e}")
        return None


# =========================
# Rendering core
# =========================
def _render_html_to_qimage(
    html: str,
    w: int,
    h: int,
    shadow: bool,
    blur_radius: int,
    shadow_color_rgba: tuple,
    shadow_offset: tuple[int, int],
) -> QImage:
    html = html or ""

    doc = QTextDocument()
    doc.setDocumentMargin(0)         # منع النزول للأسفل
    doc.setHtml(html)
    doc.setTextWidth(max(1, int(w))) # نستخدم width فقط
    # ❌ ممنوع: doc.adjustSize()

    item = QGraphicsTextItem()
    item.setDocument(doc)
    item.setDefaultTextColor(QColor(255, 255, 255, 255))
    item.setPos(0, 0)                # ثابت بدون إزاحة

    if shadow:
        eff = QGraphicsDropShadowEffect()
        eff.setBlurRadius(int(blur_radius))
        eff.setColor(QColor(*shadow_color_rgba))
        eff.setOffset(int(shadow_offset[0]), int(shadow_offset[1]))
        item.setGraphicsEffect(eff)

    scene = QGraphicsScene()
    scene.addItem(item)

    # ❗❗ لا نحسب doc_h ولا extra_bottom ولا أي شيء ديناميكي
    final_h =  int(h)                # ارتفاع ثابت لا يتغير أبداً

    scene.setSceneRect(0, 0, int(w), final_h)

    img = QImage(int(w), final_h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    scene.render(
        p,
        QRectF(0, 0, w, final_h),
        scene.sceneRect()
    )

    p.end()
    return img



def _qimage_to_bgr(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    w = img.width()
    h = img.height()
    bpl = img.bytesPerLine()

    raw = img.bits().tobytes()
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((h, bpl // 4, 4))
    arr = arr[:, :w, :]  # crop padding
    bgra = arr.copy()
    bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    return bgr


def _scale_rect(x, y, w, h, rx, ry):
    return int(x * rx), int(y * ry), int(w * rx), int(h * ry)


def render_image(
    image_path: str | None = None,
    image_name: str = "",
    text_data_list: list | None = None,
    fonts_loaded: dict | None = None,
    is_first_slide: bool = False,
    image_data=None,
    silent: bool = False,
    **kwargs,  # accept unexpected args safely
):
    """
    Render HTML labels onto image.
    Provide either image_path or image_data (OpenCV BGR numpy array).
    Returns OpenCV BGR numpy array or None.
    """
    if text_data_list is None:
        text_data_list = []
    if fonts_loaded is None:
        fonts_loaded = {}

    with _QT_LOCK:
        _ensure_qt_app()

    # 🟢 إعادة ضبط الخطوط لمنع الـ cache
        QFontDatabase.removeAllApplicationFonts()
         # Load correct fonts from config / info.txt


        if not silent:
            _dprint("=" * 80)
            _dprint(f"[Render] Image: {image_name}")
            _dprint(f"[Render] labels_count={len(text_data_list)}")
            _dprint(f"[Render] fonts_loaded={fonts_loaded}")
            _dprint(f"[Render] shadow_enabled={ENABLE_TEXT_SHADOW} blur={SHADOW_BLUR_RADIUS} "
                    f"off=({SHADOW_OFFSET_X},{SHADOW_OFFSET_Y}) color={tuple(SHADOW_COLOR)}")

        # Load cv image
        if image_data is not None:
            base_cv = image_data
        elif image_path:
            base_cv = cv2.imread(image_path)
        else:
            if not silent:
                print("[Render] No image_path or image_data provided.")
            return None

        if base_cv is None:
            if not silent:
                print("[Render] Failed to load base image.")
            return None

        base_h, base_w = base_cv.shape[:2]


        # ✅ language + flip flag
        language = (kwargs.get("language") or "en").strip().lower()
        do_flip_ar = (language == "ar") and (base_w != base_h)

        # Determine design resolution for this slide from info.txt
        # res_map = _load_resolution_map()
        # design_w, design_h = res_map.get(image_name, (base_w, base_h))
        rx = 1.0
        ry = 1.0

        # ✅ Flip base before drawing text (Arabic only)
        if do_flip_ar:
            base_cv = cv2.flip(base_cv, 1)
            base_h, base_w = base_cv.shape[:2]

        # Convert base_cv -> QImage
        rgb = cv2.cvtColor(base_cv, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, base_w, base_h, 3 * base_w, QImage.Format_RGB888)

        out_img = QImage(base_w, base_h, QImage.Format_ARGB32_Premultiplied)
        out_img.fill(Qt.transparent)

        painter = QPainter(out_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        painter.drawImage(0, 0, qimg)

        # ✅ choose font family for this slide
                # ✅ choose font family for this slide
                # ✅ choose font family for this slide
        font_family = None
        if is_first_slide and "first" in fonts_loaded:
            font_family = fonts_loaded["first"]
        elif (not is_first_slide) and "rest" in fonts_loaded:
            font_family = fonts_loaded["rest"]

        for idx, item in enumerate(text_data_list, 1):
            html = item.get("html", "") or ""
            x = int(item.get("x", 0) or 0)
            y = int(item.get("y", 0) or 0)
            ww = int(item.get("width", 400) or 400)
            hh = int(item.get("height", 200) or 200)
            gf = float(item.get("global_font", 0) or 0)

            sx, sy, sw, sh = x, y, ww, hh

            html2 = html

            html2 = html2.replace("\r\n", "\n").replace("\r", "\n")
            html2 = html2.replace("\n", "<br>")

            if font_family:
                html2 = inject_font_family(html2, font_family)

            if gf != 0:
                html2 = scale_font_sizes(html2, gf)

            html2 = make_waw_transparent(html2)

            label_img = _render_html_to_qimage(
                html=html2,
                w=max(1, sw),
                h=max(1, sh),
                shadow=bool(ENABLE_TEXT_SHADOW),
                blur_radius=int(SHADOW_BLUR_RADIUS),
                shadow_color_rgba=tuple(SHADOW_COLOR),
                shadow_offset=(int(SHADOW_OFFSET_X), int(SHADOW_OFFSET_Y)),
            )

            painter.drawImage(int(sx), int(sy), label_img)

        painter.end()

        out_bgr = _qimage_to_bgr(out_img)

        return out_bgr


# =========================
# Worker (parallel usage) - KEEP for offline only
# =========================
def render_image_worker(args):
    """
    Offline usage only (NOT recommended in API server).
    """
    (image_name, image_bytes, text_data_list, is_first_slide,
     first_font_path, rest_font_path, language, base_dir) = args

    try:
        with _QT_LOCK:
            _ensure_qt_app()

             # قراءة info.txt
            info_data = load_info_file("info.txt")  # أو المسار الصحيح

            # تحديد اللغة مباشرة لو مش جوه دالة
            language = "en"  # أو "ar"

    # تحديد الخط لكل slide
            first_font, rest_font = get_slide_fonts(
                info_data,
                image_name,   # اسم السلايد مثلاً slide_03
                language
    )

            fonts_loaded = load_custom_fonts(
                language=language,
                first_slide_font_path=first_font_path,
                rest_slides_font_path=rest_font_path,
                base_dir="",
            )

            nparr = np.frombuffer(image_bytes, np.uint8)
            img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img_cv is None:
                return (image_name, None, "Failed to decode image bytes")

            out_cv = render_image(
                image_name=image_name,
                text_data_list=text_data_list,
                fonts_loaded=fonts_loaded,
                is_first_slide=is_first_slide,
                image_data=img_cv,
                silent=True,
                language=language,   # ✅ مهم

            )
            if out_cv is None:
                return (image_name, None, "Render failed")

            ok, png = cv2.imencode(".png", out_cv)
            if not ok:
                return (image_name, None, "Failed to encode output PNG")

            return (image_name, png.tobytes(), "OK")

    except Exception as e:
        return (image_name, None, f"Worker error: {e}")