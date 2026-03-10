# -*- coding: utf-8 -*-
"""
Render story slides by drawing text from per-slide .txt files onto slide images using box metadata from info.json.

Goals (server-ready):
- Pillow (PIL) rendering only (no Qt dependency).
- Arabic shaping + RTL display using `arabic_reshaper` + `python-bidi`.
- English stays LTR.
- Exact (x,y,width,height) boxes: no coordinate shifting/scaling.
- Wrap text to box width; shrink font size if text overflows box height.
- Preserve style from info.json: font path, font size (with global_font), color, alignment.
- Robust: missing .txt files are logged and skipped (unless --strict-text).

CLI:
  python codes/render_slides_from_txt_info.py --images-dir ... --txt-dir ... --info-json ... --out-dir ...
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

try:
    import arabic_reshaper  # type: ignore
    from bidi.algorithm import get_display  # type: ignore

    _HAVE_ARABIC = True
except Exception:
    arabic_reshaper = None
    get_display = None
    _HAVE_ARABIC = False


LOG = logging.getLogger("slide_renderer")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def _is_arabic_text(s: str) -> bool:
    return bool(_ARABIC_RE.search(s or ""))


def _parse_hex_color(value: str, default: Tuple[int, int, int, int] = (255, 255, 255, 255)) -> Tuple[int, int, int, int]:
    v = (value or "").strip()
    if not v:
        return default
    if v.startswith("#"):
        v = v[1:]
    if len(v) == 3:
        v = "".join([c * 2 for c in v])
    if len(v) != 6:
        return default
    try:
        r = int(v[0:2], 16)
        g = int(v[2:4], 16)
        b = int(v[4:6], 16)
        return (r, g, b, 255)
    except Exception:
        return default


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    """
    Compatibility width measurement across Pillow versions.
    """
    if hasattr(draw, "textlength"):
        try:
            return int(round(float(draw.textlength(text, font=font))))  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return int(right - left)
    except Exception:
        return max(0, len(text) * 10)


def _line_height(font: ImageFont.FreeTypeFont) -> int:
    try:
        ascent, descent = font.getmetrics()
        return int(ascent + descent)
    except Exception:
        return int(getattr(font, "size", 16) * 1.2)


def _shape_for_display(text: str, lang: str, *, arabic_required: bool) -> str:
    if not text:
        return text
    if (lang or "").lower() != "ar":
        return text
    if not _HAVE_ARABIC:
        if arabic_required:
            raise RuntimeError("Arabic deps missing. Install `arabic_reshaper` and `python-bidi`.")
        # Fallback: keep as-is (may appear broken/reversed depending on font/Pillow).
        return text
    reshaped = arabic_reshaper.reshape(text)
    return get_display(reshaped)


def _wrap_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    *,
    lang: str,
    arabic_required: bool,
) -> List[str]:
    """
    Word wrap while measuring the *display* string (after Arabic shaping for RTL).
    Preserves explicit newlines.
    """
    out: List[str] = []
    for para in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        words = para.split(" ")
        if not words:
            out.append("")
            continue

        cur = ""
        for w in words:
            cand = w if not cur else f"{cur} {w}"
            disp = _shape_for_display(cand, lang, arabic_required=arabic_required)
            if _text_width(draw, disp, font) <= max_width:
                cur = cand
                continue

            if cur:
                out.append(cur)
                cur = w
                continue

            # Single word longer than max_width: hard-break by characters.
            chunk = ""
            for ch in w:
                cand2 = chunk + ch
                disp2 = _shape_for_display(cand2, lang, arabic_required=arabic_required)
                if _text_width(draw, disp2, font) <= max_width:
                    chunk = cand2
                else:
                    if chunk:
                        out.append(chunk)
                    chunk = ch
            cur = chunk

        out.append(cur)

    return out


def _fit_font_to_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    base_size: int,
    box_w: int,
    box_h: int,
    *,
    lang: str,
    arabic_required: bool,
    min_size: int = 8,
    line_spacing: float = 1.0,
) -> Tuple[ImageFont.FreeTypeFont, List[str], int]:
    """
    Shrink font size until wrapped text fits inside (box_w, box_h).
    Returns: (font, wrapped_lines, final_size).
    """
    size = max(int(base_size), int(min_size))
    while size >= min_size:
        font = ImageFont.truetype(str(font_path), size=size)
        lines = _wrap_text_to_width(
            draw,
            text,
            font,
            max_width=max(1, box_w),
            lang=lang,
            arabic_required=arabic_required,
        )
        lh = int(round(_line_height(font) * float(line_spacing)))
        if lh * len(lines) <= box_h:
            return font, lines, size
        size -= 1

    font = ImageFont.truetype(str(font_path), size=min_size)
    lines = _wrap_text_to_width(
        draw,
        text,
        font,
        max_width=max(1, box_w),
        lang=lang,
        arabic_required=arabic_required,
    )
    return font, lines, min_size


@dataclass(frozen=True)
class TextBox:
    x: int
    y: int
    width: int
    height: int
    align: str
    color: Tuple[int, int, int, int]
    font_path: Path
    font_size: int
    global_font: float
    lang: str  # "en" or "ar" or "" for auto
    line_spacing: float
    valign: str  # top|middle|bottom


@dataclass(frozen=True)
class SlideInfo:
    name: str
    image: str
    width: Optional[int]
    height: Optional[int]
    boxes: List[TextBox]


def _coerce_slide_num(name: str) -> int:
    try:
        return int(str(name).split("_")[1])
    except Exception:
        return 10**9


def _load_info_json(path: Path) -> List[SlideInfo]:
    """
    Supports two common schemas:
    1) {"slides":[{...slide...}, ...], "fonts": {...}}
    2) {"slide_01": {...slide...}, "slide_02": {...slide...}, ...}

    Slide object keys (best-effort):
      - name / slide_name / id
      - image / filename
      - width,height or resolution:[w,h] or size:[w,h]
      - boxes / text_boxes / labels: list of box dicts with x,y,width,height,...
    """
    raw = json.loads(path.read_text(encoding="utf-8"))

    fonts_root: Dict[str, Any] = {}
    if isinstance(raw, dict) and isinstance(raw.get("fonts"), dict):
        fonts_root = raw["fonts"]

    def resolve_font(box: Dict[str, Any]) -> str:
        # Per-box override first, then top-level "fonts".
        for k in ("font_path", "font", "fontFile", "font_file"):
            v = str(box.get(k) or "").strip()
            if v:
                return v
        # Language-specific keys
        lang = str(box.get("lang") or "").strip().lower()
        is_first = bool(box.get("is_first_slide") or False)
        if lang == "ar":
            k = "AR_FIRST_SLIDE_FONT" if is_first else "AR_REST_SLIDES_FONT"
        else:
            k = "FIRST_SLIDE_FONT" if is_first else "REST_SLIDES_FONT"
        v = str(fonts_root.get(k) or "").strip()
        if v:
            return v
        return ""

    slides: List[SlideInfo] = []

    if isinstance(raw, dict) and isinstance(raw.get("slides"), list):
        slide_items = raw["slides"]
    elif isinstance(raw, dict):
        # dict of slide_name -> slide_obj
        slide_items = []
        for k, v in raw.items():
            if isinstance(k, str) and k.lower().startswith("slide_") and isinstance(v, dict):
                obj = dict(v)
                obj.setdefault("name", k)
                slide_items.append(obj)
    else:
        raise ValueError("info.json must be a dict with 'slides' or a dict keyed by slide_XX.")

    for s in slide_items:
        if not isinstance(s, dict):
            continue

        name = str(s.get("name") or s.get("slide_name") or s.get("id") or "").strip()
        if not name:
            continue

        image_name = str(s.get("image") or s.get("filename") or f"{name}.png").strip()

        w = s.get("width")
        h = s.get("height")
        if (w is None or h is None) and isinstance(s.get("resolution"), (list, tuple)) and len(s["resolution"]) == 2:
            w, h = s["resolution"][0], s["resolution"][1]
        if (w is None or h is None) and isinstance(s.get("size"), (list, tuple)) and len(s["size"]) == 2:
            w, h = s["size"][0], s["size"][1]

        width = int(w) if isinstance(w, (int, float, str)) and str(w).strip().isdigit() else None
        height = int(h) if isinstance(h, (int, float, str)) and str(h).strip().isdigit() else None

        boxes_raw = s.get("boxes") or s.get("text_boxes") or s.get("labels") or []
        if not isinstance(boxes_raw, list):
            boxes_raw = []

        boxes: List[TextBox] = []
        for b in boxes_raw:
            if not isinstance(b, dict):
                continue

            x = int(b.get("x", 0) or 0)
            y = int(b.get("y", 0) or 0)
            bw = int(b.get("width", 0) or 0)
            bh = int(b.get("height", 0) or 0)
            if bw <= 0 or bh <= 0:
                continue

            align = str(b.get("align") or b.get("alignment") or "").strip().lower() or "left"
            color = _parse_hex_color(str(b.get("color") or b.get("fill") or "#ffffff"))

            lang = str(b.get("lang") or "").strip().lower()
            if lang not in ("en", "ar"):
                lang = ""

            font_size = int(b.get("font_size") or b.get("size") or 24)
            try:
                gf = float(b.get("global_font", 1.0) or 1.0)
            except Exception:
                gf = 1.0

            try:
                line_spacing = float(b.get("line_spacing", 1.0) or 1.0)
            except Exception:
                line_spacing = 1.0

            valign = str(b.get("valign") or "top").strip().lower()
            if valign not in ("top", "middle", "bottom"):
                valign = "top"

            font_str = resolve_font(b)
            if not font_str:
                raise ValueError(f"Missing font path for slide {name} box at ({x},{y}). Provide font_path in info.json.")
            font_path = Path(font_str)
            if not font_path.is_absolute():
                font_path = (path.parent / font_path).resolve()
            if not font_path.exists():
                raise FileNotFoundError(f"Font not found: {font_path}")

            boxes.append(
                TextBox(
                    x=x,
                    y=y,
                    width=bw,
                    height=bh,
                    align=align,
                    color=color,
                    font_path=font_path,
                    font_size=font_size,
                    global_font=gf,
                    lang=lang,
                    line_spacing=line_spacing,
                    valign=valign,
                )
            )

        slides.append(SlideInfo(name=name, image=image_name, width=width, height=height, boxes=boxes))

    slides.sort(key=lambda s: _coerce_slide_num(s.name))
    return slides


def _read_slide_txt(txt_dir: Path, slide_name: str, *, encoding: str) -> Optional[str]:
    p = txt_dir / f"{slide_name}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding=encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n").strip()


def _split_slide_text_to_boxes(text: str, boxes_count: int) -> List[str]:
    """
    Default mapping: split by blank lines into chunks and assign in order.
    If there are fewer chunks than boxes, remaining boxes get "".
    If there are more chunks than boxes, extra chunks are appended to the last box.
    """
    if boxes_count <= 0:
        return []
    if not text:
        return [""] * boxes_count

    chunks = [c.strip() for c in re.split(r"\n\s*\n+", text) if c.strip()]
    if not chunks:
        return [""] * boxes_count

    if len(chunks) == boxes_count:
        return chunks
    if len(chunks) < boxes_count:
        return chunks + ([""] * (boxes_count - len(chunks)))

    # Too many chunks: merge extras into last box
    head = chunks[: boxes_count - 1]
    tail = "\n\n".join(chunks[boxes_count - 1 :])
    return head + [tail]


def render_slides(
    *,
    images_dir: Path,
    txt_dir: Path,
    info_json: Path,
    out_dir: Path,
    strict_text: bool,
    strict_size: bool,
    arabic_required: bool,
    encoding: str,
) -> None:
    slides = _load_info_json(info_json)
    if not slides:
        raise RuntimeError("No slides found in info.json.")

    out_dir.mkdir(parents=True, exist_ok=True)

    for idx, slide in enumerate(slides, 1):
        img_path = images_dir / slide.image
        if not img_path.exists():
            # Fallback: try by slide name with common extensions
            found = None
            for ext in (".png", ".jpg", ".jpeg"):
                p2 = images_dir / f"{slide.name}{ext}"
                if p2.exists():
                    found = p2
                    break
            if found is None:
                raise FileNotFoundError(f"Slide image not found for {slide.name}: {img_path}")
            img_path = found

        img = Image.open(img_path).convert("RGBA")
        draw = ImageDraw.Draw(img)

        if slide.width is not None and slide.height is not None:
            if img.size != (slide.width, slide.height):
                msg = f"{slide.name}: image size {img.size} != info.json {(slide.width, slide.height)}"
                if strict_size:
                    raise RuntimeError(msg)
                LOG.warning(msg)

        slide_txt = _read_slide_txt(txt_dir, slide.name, encoding=encoding)
        if slide_txt is None:
            msg = f"{slide.name}: missing txt file: {txt_dir / (slide.name + '.txt')}"
            if strict_text:
                raise FileNotFoundError(msg)
            LOG.warning(msg)
            slide_txt = ""

        texts_for_boxes = _split_slide_text_to_boxes(slide_txt, len(slide.boxes))

        LOG.info("Slide %s/%s %s image=%s size=%s boxes=%d", idx, len(slides), slide.name, img_path.name, img.size, len(slide.boxes))

        for box_idx, box in enumerate(slide.boxes, 1):
            raw_text = texts_for_boxes[box_idx - 1] if box_idx - 1 < len(texts_for_boxes) else ""
            if not raw_text:
                LOG.info("  box#%d: empty text -> skip (x=%d y=%d w=%d h=%d)", box_idx, box.x, box.y, box.width, box.height)
                continue

            lang = box.lang or ("ar" if _is_arabic_text(raw_text) else "en")
            align = box.align
            if lang == "ar" and align not in ("left", "center", "right"):
                align = "right"
            if lang == "ar" and align == "left":
                # Arabic defaults to right alignment unless explicitly requested.
                align = "right"

            base_size = int(box.font_size)
            if box.global_font and box.global_font > 0:
                base_size = max(1, int(round(base_size * float(box.global_font))))

            font, wrapped_lines, final_size = _fit_font_to_box(
                draw,
                raw_text,
                box.font_path,
                base_size=base_size,
                box_w=box.width,
                box_h=box.height,
                lang=lang,
                arabic_required=arabic_required,
                min_size=8,
                line_spacing=box.line_spacing,
            )

            lh = int(round(_line_height(font) * float(box.line_spacing)))
            total_h = lh * len(wrapped_lines)
            if box.valign == "middle":
                y0 = box.y + max(0, (box.height - total_h) // 2)
            elif box.valign == "bottom":
                y0 = box.y + max(0, box.height - total_h)
            else:
                y0 = box.y

            LOG.info(
                "  box#%d lang=%s rect=(%d,%d,%d,%d) align=%s font=%s base=%d final=%d lines=%d text=%r",
                box_idx,
                lang,
                box.x,
                box.y,
                box.width,
                box.height,
                align,
                box.font_path.name,
                base_size,
                final_size,
                len(wrapped_lines),
                (raw_text[:120] + ("..." if len(raw_text) > 120 else "")),
            )

            cur_y = y0
            for line in wrapped_lines:
                disp = _shape_for_display(line, lang, arabic_required=arabic_required)
                line_w = _text_width(draw, disp, font)
                if align == "center":
                    cur_x = box.x + max(0, (box.width - line_w) // 2)
                elif align == "right":
                    cur_x = box.x + max(0, box.width - line_w)
                else:
                    cur_x = box.x

                # Draw at exact computed pixel coords; PIL clips if it overflows.
                draw.text((cur_x, cur_y), disp, font=font, fill=box.color)
                cur_y += lh

        out_path = out_dir / slide.image
        # Keep original name as in info.json; write PNG if extension is .png, otherwise preserve extension if possible.
        if out_path.suffix.lower() == ".png":
            img.save(out_path, format="PNG")
        else:
            img.convert("RGB").save(out_path)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Render text onto slide images using per-slide TXT + info.json box metadata.")
    ap.add_argument("--images-dir", required=True, help="Folder containing slide images (PNG/JPG).")
    ap.add_argument("--txt-dir", required=True, help="Folder containing per-slide .txt files (slide_01.txt, ...).")
    ap.add_argument("--info-json", required=True, help="info.json describing slides + text boxes + styles.")
    ap.add_argument("--out-dir", required=True, help="Output folder for rendered slides.")
    ap.add_argument("--encoding", default="utf-8", help="TXT file encoding (default: utf-8).")
    ap.add_argument("--strict-text", action="store_true", help="Fail if a slide .txt file is missing.")
    ap.add_argument("--allow-size-mismatch", action="store_true", help="Allow image size to differ from info.json (logs a warning).")
    ap.add_argument("--arabic-best-effort", action="store_true", help="Do not fail if Arabic deps are missing (Arabic may render incorrectly).")
    ap.add_argument("--log-level", default="INFO", help="DEBUG|INFO|WARNING|ERROR")

    args = ap.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(levelname)s %(message)s",
    )

    render_slides(
        images_dir=Path(args.images_dir),
        txt_dir=Path(args.txt_dir),
        info_json=Path(args.info_json),
        out_dir=Path(args.out_dir),
        strict_text=bool(args.strict_text),
        strict_size=not bool(args.allow_size_mismatch),
        arabic_required=not bool(args.arabic_best_effort),
        encoding=str(args.encoding),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
