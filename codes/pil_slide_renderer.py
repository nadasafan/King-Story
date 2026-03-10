# -*- coding: utf-8 -*-
"""
PIL-based slide renderer that can draw English (LTR) and Arabic (RTL) text
into slide images using layout JSON + optional per-slide/per-element .txt files.

Entry point:
    render_slide_from_txt(slide_name) -> PIL.Image.Image
"""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

try:
    # Optional; only required for correct Arabic shaping/RTL.
    import arabic_reshaper  # type: ignore
    from bidi.algorithm import get_display  # type: ignore

    _HAVE_ARABIC = True
except Exception:
    arabic_reshaper = None
    get_display = None
    _HAVE_ARABIC = False


_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def _is_arabic_text(s: str) -> bool:
    return bool(_ARABIC_RE.search(s or ""))


def _parse_color(value: str, default: Tuple[int, int, int, int] = (255, 255, 255, 255)) -> Tuple[int, int, int, int]:
    v = (value or "").strip().lower()
    if not v:
        return default
    if v.startswith("#") and len(v) in (4, 7):
        if len(v) == 4:
            r = int(v[1] * 2, 16)
            g = int(v[2] * 2, 16)
            b = int(v[3] * 2, 16)
        else:
            r = int(v[1:3], 16)
            g = int(v[3:5], 16)
            b = int(v[5:7], 16)
        return (r, g, b, 255)
    return default


def _font_size_from_css(style: str, fallback: int = 24) -> int:
    s = style or ""
    m = re.search(r"font-size\s*:\s*(\d+(?:\.\d+)?)(px|pt)?", s, flags=re.IGNORECASE)
    if not m:
        return int(fallback)
    try:
        return max(1, int(float(m.group(1))))
    except Exception:
        return int(fallback)


def _align_from_html(html: str, fallback: str = "left") -> str:
    m = re.search(r"<p[^>]*\balign\s*=\s*['\"](left|right|center)['\"]", html or "", flags=re.IGNORECASE)
    if not m:
        # CSS text-align
        m2 = re.search(r"text-align\s*:\s*(left|right|center)", html or "", flags=re.IGNORECASE)
        return (m2.group(1).lower() if m2 else fallback)
    return m.group(1).lower()


def _style_from_html(html: str) -> Dict[str, Any]:
    """
    Best-effort style extraction (dominant style) for PIL rendering.
    Preserves: color, font-size, bold, italic, align.
    """
    out: Dict[str, Any] = {}
    h = html or ""

    # Prefer first span style if present.
    m = re.search(r"<span[^>]*\bstyle\s*=\s*['\"]([^'\"]+)['\"]", h, flags=re.IGNORECASE)
    if m:
        style = m.group(1)
        out["font_size"] = _font_size_from_css(style, fallback=out.get("font_size", 24))
        cm = re.search(r"color\s*:\s*(#[0-9a-fA-F]{3,6})", style)
        if cm:
            out["color"] = cm.group(1)

        if re.search(r"font-weight\s*:\s*(700|bold)", style, flags=re.IGNORECASE):
            out["bold"] = True
        if re.search(r"font-style\s*:\s*(italic|oblique)", style, flags=re.IGNORECASE):
            out["italic"] = True

    # Strong/em tags
    if "<strong" in h.lower():
        out["bold"] = True
    if re.search(r"<(em|i)\b", h, flags=re.IGNORECASE):
        out["italic"] = True

    out["align"] = _align_from_html(h, fallback="left")
    return out


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = (tag or "").lower()
        if t in ("br",):
            self.parts.append("\n")
        elif t in ("p", "div"):
            # Paragraph break (Qt HTML often uses <p> per line)
            if self.parts and self.parts[-1] != "\n":
                self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self.parts.append(data)

    def get_text(self) -> str:
        txt = "".join(self.parts)
        # Normalize whitespace but keep line breaks.
        txt = txt.replace("\r\n", "\n").replace("\r", "\n")
        txt = re.sub(r"[ \t]+", " ", txt)
        txt = re.sub(r"\n{3,}", "\n\n", txt)
        return txt.strip()


def _html_to_plain_text(html: str) -> str:
    p = _PlainTextHTMLParser()
    p.feed(html or "")
    return p.get_text()


def _shape_arabic_line(line: str) -> str:
    if not line:
        return line
    if not _HAVE_ARABIC:
        raise RuntimeError(
            "Arabic shaping requested but dependencies are missing. "
            "Install `arabic_reshaper` and `python-bidi`."
        )
    reshaped = arabic_reshaper.reshape(line)
    return get_display(reshaped)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> List[str]:
    """
    Simple word-wrapping that preserves explicit newlines.
    """
    lines: List[str] = []
    for para in (text or "").split("\n"):
        words = para.split(" ")
        if not words:
            lines.append("")
            continue
        current = ""
        for w in words:
            candidate = (w if not current else current + " " + w)
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # If a single word is too long, hard-break it.
            if _text_width(draw, w, font) <= max_width:
                current = w
            else:
                chunk = ""
                for ch in w:
                    cand2 = chunk + ch
                    if _text_width(draw, cand2, font) <= max_width:
                        chunk = cand2
                    else:
                        if chunk:
                            lines.append(chunk)
                        chunk = ch
                current = chunk
        lines.append(current)
    return lines


def _fit_font_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    base_size: int,
    box_w: int,
    box_h: int,
    min_size: int = 8,
) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    size = int(base_size)
    size = max(min_size, size)
    while size >= min_size:
        font = ImageFont.truetype(font_path, size=size)
        lines = _wrap_text(draw, text, font, max_width=box_w)
        ascent, descent = font.getmetrics()
        line_h = ascent + descent
        total_h = line_h * len(lines)
        if total_h <= box_h:
            return font, lines
        size -= 1
    font = ImageFont.truetype(font_path, size=min_size)
    return font, _wrap_text(draw, text, font, max_width=box_w)


def _resolve_slide_image(slide_name: str) -> Path:
    img_dir = Path(os.environ.get("SLIDE_IMAGES_DIR", "")).expanduser()
    if not img_dir:
        raise FileNotFoundError("SLIDE_IMAGES_DIR is not set.")
    for ext in (".png", ".jpg", ".jpeg"):
        p = img_dir / f"{slide_name}{ext}"
        if p.exists():
            return p
    raise FileNotFoundError(f"Slide image not found for {slide_name} under {img_dir}")


def _load_layout() -> Dict[str, Any]:
    layout_path = os.environ.get("SLIDE_LAYOUT_JSON", "").strip()
    if not layout_path:
        raise FileNotFoundError("SLIDE_LAYOUT_JSON is not set.")
    p = Path(layout_path).expanduser()
    raw = p.read_text(encoding="utf-8")
    return json.loads(raw)


def _resolve_font_path(layout: Dict[str, Any], language: str, is_first: bool) -> str:
    # Prefer values from JSON if present; fall back to environment, then to config.py via absolute paths.
    lang = (language or "en").lower()
    if lang == "ar":
        key = "AR_FIRST_SLIDE_FONT" if is_first else "AR_REST_SLIDES_FONT"
    else:
        key = "FIRST_SLIDE_FONT" if is_first else "REST_SLIDES_FONT"

    val = (layout.get(key) or os.environ.get(key) or "").strip()
    if val:
        p = Path(val)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if p.exists():
            return str(p)

    # Final fallback: use repo config paths if available in environment.
    try:
        from config import (
            EN_FIRST_SLIDE_FONT,
            EN_REST_SLIDES_FONT,
            AR_FIRST_SLIDE_FONT,
            AR_REST_SLIDES_FONT,
        )

        if lang == "ar":
            return AR_FIRST_SLIDE_FONT if is_first else AR_REST_SLIDES_FONT
        return EN_FIRST_SLIDE_FONT if is_first else EN_REST_SLIDES_FONT
    except Exception as e:
        raise FileNotFoundError(f"Could not resolve font path for {key}: {e}")


def render_slide_from_txt(slide_name: str) -> Image.Image:
    """
    Renders a single slide image using a layout JSON + optional per-slide/per-element .txt content.

    Expected JSON (minimal):
      {
        "slide_01": [{"x":..,"y":..,"width":..,"height":..,"txt_file":"slide_01.txt", ...}, ...],
        "FIRST_SLIDE_FONT": "...",
        "AR_FIRST_SLIDE_FONT": "..."
      }

    If an element doesn't include `txt_file`, it falls back to `text` or `html` fields.
    """
    layout = _load_layout()
    elements = layout.get(slide_name)
    if not isinstance(elements, list):
        raise KeyError(f"Slide layout not found or not a list: {slide_name}")

    # Load base slide image
    slide_img_path = _resolve_slide_image(slide_name)
    img = Image.open(slide_img_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # Determine first-slide status using slide number if possible.
    try:
        slide_num = int(str(slide_name).split("_")[1])
    except Exception:
        slide_num = 0
    is_first = (slide_num == 1)

    text_dir = Path(os.environ.get("SLIDE_TEXT_DIR", "")).expanduser()

    for el in elements:
        if not isinstance(el, dict):
            continue

        x = int(el.get("x", 0) or 0)
        y = int(el.get("y", 0) or 0)
        w = int(el.get("width", 0) or 0)
        h = int(el.get("height", 0) or 0)
        if w <= 0 or h <= 0:
            continue

        # Resolve content
        txt = ""
        txt_file = (el.get("txt_file") or el.get("text_file") or "").strip()
        if txt_file and text_dir:
            p = (text_dir / txt_file)
            if p.exists():
                txt = p.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not txt:
            if "text" in el and isinstance(el.get("text"), str):
                txt = el.get("text", "").strip()
            elif "html" in el and isinstance(el.get("html"), str):
                txt = _html_to_plain_text(el.get("html", ""))

        if not txt:
            continue

        # Language detection / override
        lang = (el.get("lang") or "").strip().lower()
        if lang not in ("en", "ar"):
            lang = "ar" if _is_arabic_text(txt) else "en"

        # Style extraction
        style: Dict[str, Any] = {}
        if isinstance(el.get("style"), dict):
            style.update(el["style"])
        if isinstance(el.get("html"), str):
            style.update(_style_from_html(el["html"]))

        base_size = int(style.get("font_size") or el.get("font_size") or 24)
        gf = el.get("global_font", 0) or style.get("global_font", 0) or 0
        try:
            gf_val = float(gf)
        except Exception:
            gf_val = 0.0
        if gf_val and gf_val > 0:
            # Match existing pipeline semantics: global_font acts like a multiplier for declared sizes.
            base_size = max(1, int(round(base_size * gf_val)))
        align = str(style.get("align") or el.get("align") or ("right" if lang == "ar" else "left")).lower()
        color = _parse_color(str(style.get("color") or el.get("color") or "#ffffff"))
        bold = bool(style.get("bold") or el.get("bold"))

        font_path = _resolve_font_path(layout, language=lang, is_first=is_first)

        # Wrap + fit to box
        font, lines = _fit_font_size(draw, txt, font_path, base_size, w, h)

        # Apply Arabic shaping per line (RTL display) after wrapping.
        if lang == "ar":
            lines = [_shape_arabic_line(line) for line in lines]

        ascent, descent = font.getmetrics()
        line_h = ascent + descent
        total_h = line_h * len(lines)
        cur_y = y + max(0, (h - total_h) // 2)

        for line in lines:
            if lang == "ar" and align == "left":
                # Arabic defaults to right alignment unless explicitly overridden.
                align_eff = "right"
            else:
                align_eff = align

            line_w = int(_text_width(draw, line, font))
            if align_eff == "center":
                cur_x = x + max(0, (w - line_w) // 2)
            elif align_eff == "right":
                cur_x = x + max(0, w - line_w)
            else:
                cur_x = x

            # Bold approximation if only a single font file is available.
            if bold:
                draw.text((cur_x + 1, cur_y), line, font=font, fill=color)
            draw.text((cur_x, cur_y), line, font=font, fill=color)

            cur_y += line_h

    return img


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    """
    Compatibility wrapper: `textlength` is not present in some Pillow versions.
    """
    if hasattr(draw, "textlength"):
        try:
            return float(draw.textlength(text, font=font))  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return float(right - left)
    except Exception:
        return float(len(text) * 10)
