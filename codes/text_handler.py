# -*- coding: utf-8 -*-
"""
Text Handler Module (FIXED - Coordinate Scaling)

🔧 الإصلاح الرئيسي:
- تفعيل الـ scaling للإحداثيات بناءً على resolution_slides في info.txt
- النص كان مصمم على أبعاد محددة (مثلاً 2048×1024) لكن الصور الفعلية
  قد تكون بأبعاد مختلفة → كان rx=1.0 دائماً = خطأ

Debug env vars:
    TEXT_DEBUG=1
    TEXT_DEBUG_HTML=1
Optional:
    TEXT_INFO_PATH=/abs/path/to/info.txt
"""

import os
import json
import re
import threading
from pathlib import Path

import cv2
import numpy as np

# =========================
# MUST be set BEFORE PySide6 import
# =========================
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
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
# Global lock
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
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# =========================
# ✅ الجديد: تحميل resolution map من info.txt
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
    """
    يقرأ resolution_slides من info.txt ويعمل cache.
    مثال: {"slide_01": (2048, 2048), "slide_02": (2048, 1024), ...}
    """
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
        _dprint(f"[Info] Loaded resolution map: {len(res_map)} slides from {info_path}")
        return _RES_MAP_CACHE
    except Exception as e:
        _RES_MAP_CACHE = {}
        _dprint(f"[Info] Failed to read info.txt: {e}")
        return _RES_MAP_CACHE


def invalidate_resolution_cache():
    """استدعيها لو غيّرت info.txt أثناء التشغيل"""
    global _RES_MAP_CACHE
    _RES_MAP_CACHE = None


# =========================
# Fonts
# =========================
def load_custom_fonts(
    language: str,
    first_slide_font_path: str | None = None,
    rest_slides_font_path: str | None = None,
    base_dir: str | None = None
) -> dict:
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

        if first_font and os.path.exists(first_font):
            font_id = QFontDatabase.addApplicationFont(first_font)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    fonts_loaded["first"] = families[0]
                    _dprint(f"[Fonts] Loaded FIRST: {families[0]}")
        else:
            _dprint(f"[Fonts] FIRST font not found: {first_font}")

        if rest_font and os.path.exists(rest_font):
            font_id = QFontDatabase.addApplicationFont(rest_font)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    fonts_loaded["rest"] = families[0]
                    _dprint(f"[Fonts] Loaded REST: {families[0]}")
        else:
            _dprint(f"[Fonts] REST font not found: {rest_font}")

        return fonts_loaded


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

    def repl(match):
        original_size = float(match.group(1))
        unit = match.group(2) if match.group(2) else "pt"
        new_size = max(1, int(original_size * gf))
        return f"font-size:{new_size}{unit}"

    return re.sub(r"font-size:(\d+(?:\.\d+)?)(pt|px)?", repl, html_text)


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
        data = json.loads(content)

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
    doc.setDocumentMargin(0)
    doc.setHtml(html)
    doc.setTextWidth(max(1, int(w)))
    doc.adjustSize()

    item = QGraphicsTextItem()
    item.setDocument(doc)
    item.setDefaultTextColor(QColor(255, 255, 255, 255))
    item.setPos(0, 0)

    if shadow:
        eff = QGraphicsDropShadowEffect()
        eff.setBlurRadius(int(blur_radius))
        eff.setColor(QColor(*shadow_color_rgba))
        eff.setOffset(int(shadow_offset[0]), int(shadow_offset[1]))
        item.setGraphicsEffect(eff)

    scene = QGraphicsScene()
    scene.addItem(item)

    doc_h = int(doc.size().height())

    extra_bottom = 12
    if shadow:
        extra_bottom += abs(int(shadow_offset[1])) + int(blur_radius)

    final_h = max(int(h), doc_h + extra_bottom)

    scene.setSceneRect(0, 0, int(w), int(final_h))

    img = QImage(int(w), int(final_h), QImage.Format_ARGB32_Premultiplied)
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
    arr = arr[:, :w, :]
    bgra = arr.copy()
    bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
    return bgr


def _scale_rect(x, y, w, h, rx, ry):
    return int(x * rx), int(y * ry), int(w * rx), int(h * ry)


# =========================
# ✅ الدالة الرئيسية - مع الإصلاح
# =========================
def render_image(
    image_path: str | None = None,
    image_name: str = "",
    text_data_list: list | None = None,
    fonts_loaded: dict | None = None,
    is_first_slide: bool = False,
    image_data=None,
    silent: bool = False,
    **kwargs,
):
    """
    Render HTML labels onto image.
    
    🔧 الإصلاح: scaling الإحداثيات من أبعاد التصميم (info.txt) إلى أبعاد الصورة الفعلية.
    
    الإحداثيات في txt مصممة على resolution_slides في info.txt.
    إذا كانت الصورة الفعلية بأبعاد مختلفة، نحسب rx و ry ونحوّل الإحداثيات.
    """
    if text_data_list is None:
        text_data_list = []
    if fonts_loaded is None:
        fonts_loaded = {}

    with _QT_LOCK:
        _ensure_qt_app()

        if not silent:
            _dprint("=" * 80)
            _dprint(f"[Render] Image: {image_name}")
            _dprint(f"[Render] labels_count={len(text_data_list)}")

        # تحميل الصورة
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

        # =========================
        # Arabic flip logic
        # =========================
        language = (kwargs.get("language") or "en").strip().lower()

        slide_num = 1
        if "_" in image_name:
            try:
                slide_num = int(image_name.split("_")[1])
            except:
                slide_num = 1

        text_keys = kwargs.get("text_data_keys", [])
        all_nums = []
        for k in text_keys:
            if "_" in k:
                try:
                    all_nums.append(int(k.split("_")[1]))
                except:
                    pass

        last_slide = max(all_nums) if all_nums else slide_num
        is_first = (slide_num == 1)
        is_last = (slide_num == last_slide)
        do_flip_ar = (language == "ar") and (not is_first) and (not is_last)

        # =========================
        # ✅ الإصلاح الجوهري: حساب rx و ry
        # =========================
        res_map = _load_resolution_map()
        
        if image_name in res_map:
            design_w, design_h = res_map[image_name]
            rx = base_w / design_w
            ry = base_h / design_h
            if not silent or DEBUG:
                _dprint(f"[Scale] {image_name}: design=({design_w}×{design_h}) actual=({base_w}×{base_h}) scale=({rx:.4f}, {ry:.4f})")
        else:
            # لو مش موجود في info.txt، استخدم 1.0 (السلوك القديم)
            rx = 1.0
            ry = 1.0
            _dprint(f"[Scale] {image_name}: not in res_map, using rx=ry=1.0")

        # Flip base before drawing text (Arabic only)
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

        # اختيار الخط
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

            # ✅ تطبيق الـ scaling على الإحداثيات والأبعاد
            sx, sy, sw, sh = _scale_rect(x, y, ww, hh, rx, ry)

            # ✅ تصحيح: لو y سالب، نبدأ من 0 مع تعديل الارتفاع
            # (y سالب يعني النص مصمم ليبدأ فوق الصورة - نتجاهل الجزء فوق الصورة)
            if sy < 0:
                # نقلص الارتفاع بمقدار ما كان سالباً ثم نبدأ من 0
                sh = max(1, sh + sy)
                sy = 0

            # ✅ تصحيح: لو x سالب، نبدأ من 0
            if sx < 0:
                sw = max(1, sw + sx)
                sx = 0

            # ✅ ضمان أن الـ label لا يتجاوز حدود الصورة
            if sx >= base_w or sy >= base_h:
                _dprint(f"[Render] Label {idx} ({image_name}): SKIPPED (out of bounds: sx={sx}, sy={sy}, img={base_w}×{base_h})")
                continue

            # تحديد العرض الفعلي بحيث لا يتجاوز حدود الصورة
            sw = min(sw, base_w - sx)
            sh = min(sh, base_h - sy)

            if sw <= 0 or sh <= 0:
                continue

            html2 = html
            html2 = html2.replace("\r\n", "\n").replace("\r", "\n")
            html2 = html2.replace("\n", "<br>")

            if font_family:
                html2 = inject_font_family(html2, font_family)

            if gf != 0:
                # ✅ scale the global_font مع الـ rx/ry
                # global_font هو multiplier للخط، نضرب في min(rx,ry) لو الصورة أصغر
                scale_factor = min(rx, ry)
                gf_scaled = gf * scale_factor if scale_factor < 1.0 else gf
                html2 = scale_font_sizes(html2, gf_scaled)

            html2 = make_waw_transparent(html2)

            _dprint(f"[Render] Label {idx} ({image_name}): pos=({sx},{sy}) size=({sw}×{sh}) gf={gf}")
            if DEBUG_HTML:
                _dprint(f"[Render] html: {_short(html2)}")

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
# Worker (parallel usage)
# =========================
def render_image_worker(args):
    (image_name, image_bytes, text_data_list, is_first_slide,
     first_font_path, rest_font_path, language, base_dir) = args

    try:
        with _QT_LOCK:
            _ensure_qt_app()

            fonts_loaded = load_custom_fonts(
                language=language,
                first_slide_font_path=first_font_path,
                rest_slides_font_path=rest_font_path,
                base_dir=base_dir,
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
                language=language,
            )
            if out_cv is None:
                return (image_name, None, "Render failed")

            ok, png = cv2.imencode(".png", out_cv)
            if not ok:
                return (image_name, None, "Failed to encode output PNG")

            return (image_name, png.tobytes(), "OK")

    except Exception as e:
        return (image_name, None, f"Worker error: {e}")
    