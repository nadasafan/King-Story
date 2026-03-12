# -*- coding: utf-8 -*-
"""
Text Handler Module (FINAL - API SAFE)

✅ Fixes for FastAPI/uvicorn:
- Forces Qt to run OFFSCREEN (no GUI) BEFORE importing PySide6
- Ensures QApplication exists BEFORE using QFontDatabase
- Uses a global lock because Qt painting is NOT thread-safe
- Stable HTML render using QTextDocument + QGraphicsScene (shadow supported)
"""
import os
import json
import re
import threading
import copy

import cv2
import numpy as np

# =========================
# MUST be set BEFORE PySide6 import
# =========================
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")
os.environ.setdefault("QT_LOGGING_RULES", "*.debug=false;qt.qpa.*=false")

from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QGraphicsScene, QGraphicsTextItem
from PySide6.QtGui import QFontDatabase, QColor, QImage, QPainter, QTextDocument
from PySide6.QtCore import Qt, QRectF

from config import (
    EN_FIRST_SLIDE_FONT, EN_REST_SLIDES_FONT,
    AR_FIRST_SLIDE_FONT, AR_REST_SLIDES_FONT,
    ENABLE_TEXT_SHADOW, SHADOW_BLUR_RADIUS, SHADOW_COLOR, SHADOW_OFFSET_X, SHADOW_OFFSET_Y
)

# =========================
# Global lock (Qt is not thread-safe)
# =========================
_QT_LOCK = threading.Lock()

# =========================
# Debug
# =========================
DEBUG = os.environ.get("TEXT_DEBUG", "0").strip().lower() in ("1", "true", "yes", "y")

def _dprint(msg: str):
    if DEBUG:
        print(msg)

# =========================
# Qt App
# =========================
def _ensure_qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

# =========================
# Fonts
# =========================
def load_custom_fonts(language, first_slide_font_path=None, rest_slides_font_path=None, base_dir=None):
    with _QT_LOCK:
        _ensure_qt_app()
        fonts_loaded = {}

        # First slide font
        first_font = first_slide_font_path or (EN_FIRST_SLIDE_FONT if language == "en" else AR_FIRST_SLIDE_FONT)
        if base_dir:
            first_font = os.path.join(base_dir, first_font)
        if os.path.exists(first_font):
            font_id = QFontDatabase.addApplicationFont(first_font)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    fonts_loaded["first"] = families[0]
                    _dprint(f"[Fonts] Loaded FIRST: {families[0]}")
        else:
            _dprint(f"[Fonts] FIRST font not found: {first_font}")

        # Rest slides font
        rest_font = rest_slides_font_path or (EN_REST_SLIDES_FONT if language == "en" else AR_REST_SLIDES_FONT)
        if base_dir:
            rest_font = os.path.join(base_dir, rest_font)
        if os.path.exists(rest_font):
            font_id = QFontDatabase.addApplicationFont(rest_font)
            if font_id != -1:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    fonts_loaded["rest"] = families[0]
                    _dprint(f"[Fonts] Loaded REST: {families[0]}")
        else:
            _dprint(f"[Fonts] REST font not found: {rest_font}")

        return fonts_loaded

def get_slide_fonts(info: dict, slide_name: str, language: str):
    slide_fonts = info.get("fonts", {}).get(slide_name, {})
    first_font = slide_fonts.get("first") or (EN_FIRST_SLIDE_FONT if language == "en" else AR_FIRST_SLIDE_FONT)
    rest_font  = slide_fonts.get("rest") or (EN_REST_SLIDES_FONT if language == "en" else AR_REST_SLIDES_FONT)
    return first_font, rest_font

# =========================
# HTML helpers
# =========================
def inject_font_family(html_text, font_family):
    if not font_family:
        return html_text
    html_text = re.sub(r'font-family:\s*[^;]+;', '', html_text)
    html_text = re.sub(r'style="([^"]*)"', lambda m: f'style="font-family:\'{font_family}\' !important; {m.group(1)}"', html_text)
    return html_text

def scale_font_sizes(html_text, global_font):
    if not global_font:
        return html_text
    def repl(m):
        size = float(m.group(1))
        unit = m.group(2) or "pt"
        new_size = int(size * global_font)
        return f"font-size:{new_size}{unit}"
    return re.sub(r"font-size:(\d+(?:\.\d+)?)(pt|px)?", repl, html_text)

def make_waw_transparent(html_text):
    return re.sub(
        r"(<span[^>]*color:[^>]*>)\s*و\s*(</span>)",
        lambda m: m.group(1).replace("color:#000000", "color:transparent") + "و" + m.group(2),
        html_text
    )

def replace_name_in_html(html_text, user_name, is_first_slide=False, language="en"):
    if not user_name:
        return html_text
    repl = user_name.upper() if is_first_slide else user_name
    if language == "en":
        html_text = html_text.replace("[*NAME*]", repl).replace("[*Name*]", repl)
    else:
        html_text = html_text.replace("[*الاسم*]", repl)
    return html_text

# =========================
# Rendering core
# =========================
def _render_html_to_qimage(html, w, h, shadow, blur_radius, shadow_color_rgba, shadow_offset):
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    doc.setHtml(html)
    doc.setTextWidth(max(1, int(w)))
    doc.adjustSize()

    item = QGraphicsTextItem()
    item.setDocument(doc)
    item.setDefaultTextColor(QColor(255, 255, 255, 255))
    item.setPos(0,0)

    if shadow:
        eff = QGraphicsDropShadowEffect()
        eff.setBlurRadius(int(blur_radius))
        eff.setColor(QColor(*shadow_color_rgba))
        eff.setOffset(*shadow_offset)
        item.setGraphicsEffect(eff)

    scene = QGraphicsScene()
    scene.addItem(item)

    final_h = max(int(h), int(doc.size().height()) + 10)
    scene.setSceneRect(0, 0, int(w), final_h)

    img = QImage(int(w), final_h, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    scene.render(painter, QRectF(0, 0, w, final_h), scene.sceneRect())
    painter.end()
    return img

def _qimage_to_bgr(img):
    img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    w,h = img.width(), img.height()
    arr = np.frombuffer(img.bits().tobytes(), dtype=np.uint8).reshape((h, img.bytesPerLine()//4, 4))[:, :w, :]
    return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

# =========================
# Main render
# =========================
def render_image(image_path=None, image_name="", text_data_list=None, fonts_loaded=None, is_first_slide=False, image_data=None, silent=False, **kwargs):
    text_data_list = text_data_list or []
    fonts_loaded = fonts_loaded or {}

    with _QT_LOCK:
        _ensure_qt_app()
        if image_data is not None:
            base_cv = image_data
        elif image_path:
            base_cv = cv2.imread(image_path)
        else:
            return None
        if base_cv is None:
            return None

        language = (kwargs.get("language") or "en").strip().lower()
        if language == "ar" and base_cv.shape[1] != base_cv.shape[0]:
            base_cv = cv2.flip(base_cv, 1)

        h,w = base_cv.shape[:2]
        qimg = QImage(cv2.cvtColor(base_cv, cv2.COLOR_BGR2RGB).data, w, h, 3*w, QImage.Format_RGB888)

        out_img = QImage(w,h,QImage.Format_ARGB32_Premultiplied)
        out_img.fill(Qt.transparent)
        painter = QPainter(out_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.drawImage(0,0,qimg)

        font_family = fonts_loaded.get("first") if is_first_slide else fonts_loaded.get("rest")

        for item in text_data_list:
            html = item.get("html","")
            html = html.replace("\r\n","\n").replace("\n","<br>")
            html = inject_font_family(html, font_family)
            if item.get("global_font",0):
                html = scale_font_sizes(html, float(item.get("global_font",0)))
            html = make_waw_transparent(html)

            label_img = _render_html_to_qimage(
                html=html,
                w=int(item.get("width",400)),
                h=int(item.get("height",200)),
                shadow=bool(ENABLE_TEXT_SHADOW),
                blur_radius=int(SHADOW_BLUR_RADIUS),
                shadow_color_rgba=tuple(SHADOW_COLOR),
                shadow_offset=(int(SHADOW_OFFSET_X), int(SHADOW_OFFSET_Y))
            )
            painter.drawImage(int(item.get("x",0)), int(item.get("y",0)), label_img)

        painter.end()
        return _qimage_to_bgr(out_img)

# =========================
# Worker
# =========================
def render_image_worker(args):
    (image_name, image_bytes, text_data_list, is_first_slide,
     first_font_path, rest_font_path, language, base_dir) = args

    try:
        with _QT_LOCK:
            _ensure_qt_app()

            # قراءة info.txt
            info_data = load_info_file("info.txt")

            # تحديد الخط لكل slide
            first_font, rest_font = get_slide_fonts(info_data, image_name, language)
            fonts_loaded = load_custom_fonts(
                language=language,
                first_slide_font_path=first_font_path or first_font,
                rest_slides_font_path=rest_font_path or rest_font,
                base_dir=base_dir or ""
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
                language=language
            )

            if out_cv is None:
                return (image_name, None, "Render failed")

            ok, png = cv2.imencode(".png", out_cv)
            if not ok:
                return (image_name, None, "Failed to encode output PNG")

            return (image_name, png.tobytes(), "OK")

    except Exception as e:
        return (image_name, None, f"Worker error: {e}")