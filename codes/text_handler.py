# -*- coding: utf-8 -*-
import os
import json
import threading
import copy

import cv2
import numpy as np
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QGraphicsScene, QGraphicsTextItem
from PySide6.QtGui import QFontDatabase, QColor, QImage, QPainter, QTextDocument
from PySide6.QtCore import Qt, QRectF

from config import (
    EN_FIRST_SLIDE_FONT, EN_REST_SLIDES_FONT,
    AR_FIRST_SLIDE_FONT, AR_REST_SLIDES_FONT,
    ENABLE_TEXT_SHADOW,
    SHADOW_BLUR_RADIUS, SHADOW_COLOR, SHADOW_OFFSET_X, SHADOW_OFFSET_Y,
)

_QT_LOCK = threading.Lock()

# =========================
# QApplication
# =========================
def _ensure_qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app

# =========================
# Load fonts safely
# =========================
def load_custom_fonts(language: str) -> dict:
    with _QT_LOCK:
        _ensure_qt_app()
        fonts_loaded = {}

        first_font = EN_FIRST_SLIDE_FONT if language == "en" else AR_FIRST_SLIDE_FONT
        rest_font  = EN_REST_SLIDES_FONT if language == "en" else AR_REST_SLIDES_FONT

        for key, font_path in [("first", first_font), ("rest", rest_font)]:
            if font_path and os.path.exists(font_path):
                font_id = QFontDatabase.addApplicationFont(font_path)
                if font_id != -1:
                    families = QFontDatabase.applicationFontFamilies(font_id)
                    if families:
                        fonts_loaded[key] = families[0]
        return fonts_loaded

# =========================
# Render HTML onto image safely
# =========================
def render_image_safe(
    image_data: np.ndarray,
    text_data_list: list,
    fonts_loaded: dict,
    is_first_slide: bool = False,
    language: str = "en"
) -> np.ndarray:
    if image_data is None or not isinstance(image_data, np.ndarray):
        return None

    with _QT_LOCK:
        _ensure_qt_app()

        base_cv = image_data.copy()
        base_h, base_w = base_cv.shape[:2]

        # Arabic flip if needed
        do_flip_ar = (language.lower() == "ar") and (base_w != base_h)
        if do_flip_ar:
            base_cv = cv2.flip(base_cv, 1)
            base_h, base_w = base_cv.shape[:2]

        # Convert OpenCV BGR -> QImage
        rgb = cv2.cvtColor(base_cv, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, base_w, base_h, 3 * base_w, QImage.Format_RGB888)

        out_img = QImage(base_w, base_h, QImage.Format_ARGB32_Premultiplied)
        out_img.fill(Qt.transparent)

        painter = QPainter(out_img)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.drawImage(0, 0, qimg)

        # Choose font
        font_family = fonts_loaded.get("first") if is_first_slide else fonts_loaded.get("rest")

        for item in text_data_list:
            html = item.get("html", "")
            x = int(item.get("x", 0))
            y = int(item.get("y", 0))
            w = int(item.get("width", 400))
            h = int(item.get("height", 200))

            # Simple HTML adjustments
            html = html.replace("\n", "<br>")
            if font_family:
                html = f'<div style="font-family:{font_family};">{html}</div>'

            # QTextDocument
            doc = QTextDocument()
            doc.setDocumentMargin(0)
            doc.setHtml(html)
            doc.setTextWidth(max(1, w))

            text_item = QGraphicsTextItem()
            text_item.setDocument(doc)
            text_item.setPos(0, 0)
            text_item.setDefaultTextColor(QColor(255, 255, 255, 255))

            if ENABLE_TEXT_SHADOW:
                eff = QGraphicsDropShadowEffect()
                eff.setBlurRadius(int(SHADOW_BLUR_RADIUS))
                eff.setColor(QColor(*SHADOW_COLOR))
                eff.setOffset(int(SHADOW_OFFSET_X), int(SHADOW_OFFSET_Y))
                text_item.setGraphicsEffect(eff)

            scene = QGraphicsScene()
            scene.addItem(text_item)
            scene.setSceneRect(0, 0, w, h)

            label_img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            label_img.fill(Qt.transparent)

            painter2 = QPainter(label_img)
            painter2.setRenderHint(QPainter.Antialiasing, True)
            painter2.setRenderHint(QPainter.TextAntialiasing, True)
            scene.render(painter2)
            painter2.end()

            painter.drawImage(x, y, label_img)

        painter.end()

        # Convert back to OpenCV BGR
        out_cv = out_img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        w = out_cv.width()
        h = out_cv.height()
        bpl = out_cv.bytesPerLine()
        arr = np.frombuffer(out_cv.bits().tobytes(), dtype=np.uint8).reshape((h, bpl // 4, 4))
        bgra = arr[:, :w, :]
        bgr = cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)
        return bgr