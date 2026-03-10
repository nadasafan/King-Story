# -*- coding: utf-8 -*-
"""
Render preformatted HTML blocks (Arabic + English) onto slide images.

Key goals:
- Do NOT change the HTML content (spacing/line-breaks/tags/styles). Only replace [*NAME*].
- Do NOT resize or flip images. Output pixels must match input pixels exactly.
- Render each HTML block into its (x,y,width,height) box.
- Log every action and verification to console and `--out-dir/--log-name`.

Inputs (per slide):
- Arabic HTML JSON:  ar_text_data/slide_XX.json  (default --ar-html-dir)
- English HTML JSON: en_text_data/slide_XX.json  (default --en-html-dir)
- Slide image:       slide_XX.(png|jpg|jpeg) from --slides-dir
- Metadata:          info.json (loaded for slide ordering / optional expected size warnings)

Expected HTML JSON format (flexible):
- A list of blocks: [ {...}, {...} ]
- Or an object with "blocks": [ {...}, {...} ]

Each block may contain:
- Required: x, y, width, height
- Required: html (or content/text_html)
- Optional: font (path), font_size, global_font, color, align, lang

Rendering:
- Uses a headless Qt QTextDocument HTML renderer (PySide6 preferred, PyQt5 fallback).
- For Arabic, Qt handles shaping + RTL correctly. We intentionally do not run python-bidi/arabi_reshaper
  because that would modify the HTML text content (contrary to requirements) and can double-reorder in Qt.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PIL import Image

LOG = logging.getLogger("render_html_slides")

_SLIDE_RE = re.compile(r"^slide_(\d+)$", re.IGNORECASE)
_NAME_PLACEHOLDER_RE = re.compile(r"\[\*\s*name\s*\*\]|\[\s*name\s*\*\]", re.IGNORECASE)

try:
    import arabic_reshaper  # type: ignore  # noqa: F401
    from bidi.algorithm import get_display  # type: ignore  # noqa: F401

    _HAVE_ARABIC_LIBS = True
except Exception:
    _HAVE_ARABIC_LIBS = False


def _slide_num(slide_name: str) -> int:
    m = _SLIDE_RE.match((slide_name or "").strip())
    if not m:
        return 10**9
    try:
        return int(m.group(1))
    except Exception:
        return 10**9


def _setup_logging(out_dir: Path, log_name: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / log_name
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    return log_path


def _apply_name_placeholder(s: str, *, name: str) -> str:
    if not s:
        return s
    if not name:
        return s
    return _NAME_PLACEHOLDER_RE.sub(name, s)


def _resolve_slide_image(slides_dir: Path, slide_name: str) -> Optional[Path]:
    for ext in (".png", ".jpg", ".jpeg"):
        p = slides_dir / f"{slide_name}{ext}"
        if p.exists():
            return p
    return None


def _iter_slide_names_from_images(slides_dir: Path) -> List[str]:
    names: List[str] = []
    for p in slides_dir.iterdir():
        if not p.is_file():
            continue
        if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        stem = p.stem
        if _SLIDE_RE.match(stem):
            names.append(stem)
    return sorted(set(names), key=_slide_num)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _extract_blocks(obj: Any) -> List[Dict[str, Any]]:
    if obj is None:
        return []
    if isinstance(obj, list):
        return [b for b in obj if isinstance(b, dict)]
    if isinstance(obj, dict):
        if isinstance(obj.get("blocks"), list):
            return [b for b in obj["blocks"] if isinstance(b, dict)]
    return []


def _parse_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return int(default)


def _parse_float(v: Any, default: float = 1.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _parse_align(v: Any, default: str = "left") -> str:
    s = str(v or default).strip().lower()
    if s in ("left", "center", "right", "justify"):
        return s
    return str(default).strip().lower()


def _parse_hex_rgb(value: Any) -> Optional[str]:
    """
    Return '#rrggbb' or None. (Qt can consume CSS colors.)
    """
    s = str(value or "").strip()
    if not s:
        return None
    if not s.startswith("#"):
        return None
    hexv = s[1:]
    if len(hexv) == 3:
        hexv = "".join([c * 2 for c in hexv])
    if len(hexv) != 6:
        return None
    if not re.fullmatch(r"[0-9a-fA-F]{6}", hexv):
        return None
    return f"#{hexv.lower()}"


@dataclass(frozen=True)
class HtmlBlock:
    x: int
    y: int
    width: int
    height: int
    html: str
    font_path: Optional[Path]
    font_size: Optional[int]
    global_font: float
    color_css: Optional[str]
    align: str
    lang: str  # "ar" | "en"


def _parse_html_blocks(blocks: List[Dict[str, Any]], *, base_dir: Path, lang: str, name: str) -> List[HtmlBlock]:
    out: List[HtmlBlock] = []
    for i, b in enumerate(blocks, 1):
        x = _parse_int(b.get("x", 0), 0)
        y = _parse_int(b.get("y", 0), 0)
        w = _parse_int(b.get("width", 0), 0)
        h = _parse_int(b.get("height", 0), 0)
        if w <= 0 or h <= 0:
            continue

        html = b.get("html")
        if html is None:
            html = b.get("content")
        if html is None:
            html = b.get("text_html")
        if html is None:
            # Hard fail for this block; nothing to render.
            continue
        html = str(html)
        html = _apply_name_placeholder(html, name=name)

        font_path = None
        font_str = str(b.get("font") or b.get("font_path") or "").strip()
        if font_str:
            p = Path(font_str)
            if not p.is_absolute():
                p = (base_dir / p).resolve()
            font_path = p

        font_size = None
        if b.get("font_size") is not None:
            font_size = _parse_int(b.get("font_size"), 0)
        elif b.get("size") is not None:
            font_size = _parse_int(b.get("size"), 0)
        if font_size is not None and font_size <= 0:
            font_size = None

        gf = _parse_float(b.get("global_font", 1.0), 1.0)
        if gf <= 0:
            gf = 1.0

        color_css = _parse_hex_rgb(b.get("color"))
        align = _parse_align(b.get("align") or b.get("alignment") or ("right" if lang == "ar" else "left"), "left")
        blk_lang = str(b.get("lang") or lang).strip().lower()
        if blk_lang not in ("ar", "en"):
            blk_lang = lang

        out.append(
            HtmlBlock(
                x=x,
                y=y,
                width=w,
                height=h,
                html=html,
                font_path=font_path,
                font_size=font_size,
                global_font=gf,
                color_css=color_css,
                align=align,
                lang=blk_lang,
            )
        )
    return out


def _init_qt() -> Tuple[Any, Any, Any, Any, Any, Any, Any, Any]:
    """
    Returns Qt symbols:
      (QApplication, Qt, QSizeF, QImage, QPainter, QTextDocument, QFontDatabase, QFont)

    This is done lazily so the script can print a clear error if Qt isn't installed.
    """
    # Headless-friendly defaults.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")

    try:
        from PySide6.QtCore import Qt, QSizeF  # type: ignore
        from PySide6.QtGui import QFont, QFontDatabase, QImage, QPainter  # type: ignore
        from PySide6.QtWidgets import QApplication  # type: ignore
        from PySide6.QtGui import QTextDocument  # type: ignore

        return QApplication, Qt, QSizeF, QImage, QPainter, QTextDocument, QFontDatabase, QFont
    except Exception:
        try:
            from PyQt5.QtCore import Qt, QSizeF  # type: ignore
            from PyQt5.QtGui import QFont, QFontDatabase, QImage, QPainter, QTextDocument  # type: ignore
            from PyQt5.QtWidgets import QApplication  # type: ignore

            return QApplication, Qt, QSizeF, QImage, QPainter, QTextDocument, QFontDatabase, QFont
        except Exception as e:
            raise RuntimeError(
                "Qt bindings not found. Install one of: PySide6 or PyQt5. "
                "HTML rendering requires Qt."
            ) from e


def _pil_from_qimage(qimg: Any) -> Image.Image:
    """
    Convert QImage (ARGB32 premultiplied) to PIL RGBA without DPI scaling.
    """
    w = int(qimg.width())
    h = int(qimg.height())
    bpl = int(qimg.bytesPerLine())
    ptr = qimg.bits()
    try:
        ptr.setsize(bpl * h)
    except Exception:
        # Some bindings don't require setsize.
        pass
    buf = bytes(ptr)
    # QImage Format_ARGB32* is BGRA byte order on little-endian.
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", bpl, 1).copy()


def _qt_alignment(Qt: Any, align: str) -> Any:
    if align == "center":
        return Qt.AlignHCenter
    if align == "right":
        return Qt.AlignRight
    if align == "justify":
        return Qt.AlignJustify
    return Qt.AlignLeft


def _render_html_block_to_pil(
    Qt: Any,
    QSizeF: Any,
    QImage: Any,
    QPainter: Any,
    QTextDocument: Any,
    QFontDatabase: Any,
    QFont: Any,
    *,
    block: HtmlBlock,
) -> Tuple[Image.Image, Tuple[float, float]]:
    """
    Render HTML into an RGBA PIL image exactly sized to (block.width, block.height).
    Returns (image, (doc_w, doc_h)) so caller can warn if doc exceeds box.
    """
    doc = QTextDocument()

    # Default style sheet (doesn't modify the HTML string itself).
    # We only apply defaults when provided; HTML inline styles still win.
    css_parts: List[str] = []
    if block.color_css:
        css_parts.append(f"body {{ color: {block.color_css}; }}")
    if css_parts:
        doc.setDefaultStyleSheet("\n".join(css_parts))

    # Alignment and text direction defaults.
    opt = doc.defaultTextOption()
    opt.setAlignment(_qt_alignment(Qt, block.align))
    if block.lang == "ar":
        opt.setTextDirection(Qt.RightToLeft)
    else:
        opt.setTextDirection(Qt.LeftToRight)
    doc.setDefaultTextOption(opt)

    # Default font sizing must be deterministic (px), even if font path is missing.
    # This does not change the HTML string; it only sets defaults when HTML doesn't override.
    f = QFont()
    if block.font_size:
        px = max(1, int(round(float(block.font_size) * float(block.global_font))))
        f.setPixelSize(px)

    # If a font file is provided and loadable, prefer its family.
    if block.font_path and block.font_path.exists():
        fid = QFontDatabase.addApplicationFont(str(block.font_path))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid != -1 else []
        if fams:
            f.setFamily(fams[0])

    doc.setDefaultFont(f)

    # Set HTML exactly as provided (only placeholder replacement already applied).
    doc.setHtml(block.html)

    # Ensure wrapping/layout is constrained to the box width/height.
    doc.setTextWidth(float(block.width))
    doc.setPageSize(QSizeF(float(block.width), float(block.height)))

    # Render to transparent QImage.
    qimg = QImage(int(block.width), int(block.height), QImage.Format_ARGB32_Premultiplied)
    qimg.fill(Qt.transparent)
    painter = QPainter(qimg)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    doc.drawContents(painter)
    painter.end()

    size = doc.size()
    doc_w = float(getattr(size, "width")())
    doc_h = float(getattr(size, "height")())
    return _pil_from_qimage(qimg), (doc_w, doc_h)


def _paste_rgba(base: Image.Image, overlay: Image.Image, *, x: int, y: int) -> None:
    """
    Paste overlay into base at (x,y) using overlay alpha, with clipping for negative/out-of-bounds coords.
    """
    if overlay.mode != "RGBA":
        overlay = overlay.convert("RGBA")
    if base.mode != "RGBA":
        raise ValueError("base image must be RGBA")

    bw, bh = base.size
    ow, oh = overlay.size

    # Compute intersection rectangle in base coords.
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(bw, x + ow)
    y1 = min(bh, y + oh)
    if x1 <= x0 or y1 <= y0:
        return

    # Crop overlay if needed.
    ox0 = x0 - x
    oy0 = y0 - y
    ox1 = ox0 + (x1 - x0)
    oy1 = oy0 + (y1 - y0)
    tile = overlay.crop((ox0, oy0, ox1, oy1))
    base.paste(tile, (x0, y0), tile)


def _load_expected_sizes_from_info(info_json: Path) -> Dict[str, Tuple[int, int]]:
    """
    Best-effort: if info.json contains resolution_slides or slide_sizes, return expected sizes for warnings.
    """
    out: Dict[str, Tuple[int, int]] = {}
    try:
        raw = _load_json(info_json)
    except Exception:
        return out
    if not isinstance(raw, dict):
        return out

    rs = raw.get("resolution_slides")
    if isinstance(rs, list):
        for el in rs:
            if not (isinstance(el, (list, tuple)) and len(el) >= 3):
                continue
            name = str(el[0])
            try:
                w = int(el[1])
                h = int(el[2])
            except Exception:
                continue
            if _SLIDE_RE.match(name) and w > 0 and h > 0:
                out[name] = (w, h)

    ss = raw.get("slide_sizes")
    if isinstance(ss, dict):
        for name, v in ss.items():
            if not _SLIDE_RE.match(str(name)):
                continue
            w = h = None
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                try:
                    w = int(v[0])
                    h = int(v[1])
                except Exception:
                    w = h = None
            elif isinstance(v, dict):
                try:
                    w = int(v.get("width"))
                    h = int(v.get("height"))
                except Exception:
                    w = h = None
            if w and h and w > 0 and h > 0:
                out[str(name)] = (w, h)

    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Render preformatted HTML blocks from ar/en JSON onto slide images.")
    ap.add_argument("--slides-dir", required=True, help="Folder with slide_XX images.")
    ap.add_argument("--info-json", required=True, help="Path to info.json (used for warnings/ordering only).")
    ap.add_argument("--ar-html-dir", default="ar_text_data", help="Folder with Arabic slide_XX.json (default: ar_text_data).")
    ap.add_argument("--en-html-dir", default="en_text_data", help="Folder with English slide_XX.json (default: en_text_data).")
    ap.add_argument("--out-dir", default="output", help="Output folder (default: ./output).")
    ap.add_argument("--log-name", default="log.txt", help="Log file name inside out-dir (default: log.txt).")
    ap.add_argument("--name", default="", help="Replacement for [*NAME*] placeholders.")
    args = ap.parse_args(argv)

    slides_dir = Path(args.slides_dir)
    info_json = Path(args.info_json)
    ar_dir = Path(args.ar_html_dir)
    en_dir = Path(args.en_html_dir)
    out_dir = Path(args.out_dir)

    log_path = _setup_logging(out_dir, args.log_name)
    LOG.info("Logs -> %s", log_path)
    LOG.info("Arabic libs present (not applied to HTML): %s", _HAVE_ARABIC_LIBS)

    expected_sizes = _load_expected_sizes_from_info(info_json)
    if expected_sizes:
        LOG.info("Expected sizes loaded from info.json for %d slides (warnings only).", len(expected_sizes))
    else:
        LOG.info("No expected sizes found in info.json (or unable to parse).")

    slide_names = _iter_slide_names_from_images(slides_dir)
    if not slide_names:
        raise SystemExit(f"No slide_XX images found in: {slides_dir}")

    first_slide = slide_names[0]
    last_slide = slide_names[-1]
    LOG.info("First slide: %s (copy unchanged)", first_slide)
    LOG.info("Last  slide: %s (copy unchanged)", last_slide)

    # Initialize Qt once.
    QApplication, Qt, QSizeF, QImage, QPainter, QTextDocument, QFontDatabase, QFont = _init_qt()
    app = QApplication.instance() or QApplication([])  # noqa: F841

    for idx, slide_name in enumerate(slide_names, 1):
        img_path = _resolve_slide_image(slides_dir, slide_name)
        if img_path is None:
            LOG.warning("Slide %s: missing image -> skip", slide_name)
            continue

        out_path = out_dir / img_path.name

        # Skip first/last slides (copy unchanged).
        if slide_name == first_slide or slide_name == last_slide:
            in_size = Image.open(img_path).size
            shutil.copyfile(img_path, out_path)
            out_size = Image.open(out_path).size
            LOG.info("Slide %s/%s %s: copied unchanged -> %s", idx, len(slide_names), slide_name, out_path.name)
            if out_size != in_size:
                LOG.warning("Slide %s: copied output size mismatch: %s -> %s", slide_name, in_size, out_size)
            continue

        ar_path = ar_dir / f"{slide_name}.json"
        en_path = en_dir / f"{slide_name}.json"
        have_any = ar_path.exists() or en_path.exists()
        if not have_any:
            in_size = Image.open(img_path).size
            shutil.copyfile(img_path, out_path)
            out_size = Image.open(out_path).size
            LOG.warning("Slide %s: missing both HTML JSON files -> copy unchanged", slide_name)
            if out_size != in_size:
                LOG.warning("Slide %s: copied output size mismatch: %s -> %s", slide_name, in_size, out_size)
            continue

        # Load image as RGBA (no resizing).
        img0 = Image.open(img_path)
        orig_size = img0.size
        img = img0.convert("RGBA")
        if slide_name in expected_sizes and expected_sizes[slide_name] != orig_size:
            LOG.warning("Slide %s: image size %s != expected %s (keeping original)", slide_name, orig_size, expected_sizes[slide_name])

        blocks_all: List[HtmlBlock] = []

        if ar_path.exists():
            try:
                ar_obj = _load_json(ar_path)
                ar_blocks_raw = _extract_blocks(ar_obj)
                blocks_all.extend(_parse_html_blocks(ar_blocks_raw, base_dir=ar_path.parent, lang="ar", name=str(args.name or "")))
            except Exception as e:
                LOG.warning("Slide %s: failed to load Arabic JSON (%s): %s", slide_name, ar_path.name, e)
        else:
            LOG.info("Slide %s: Arabic JSON missing (%s)", slide_name, ar_path.name)

        if en_path.exists():
            try:
                en_obj = _load_json(en_path)
                en_blocks_raw = _extract_blocks(en_obj)
                blocks_all.extend(_parse_html_blocks(en_blocks_raw, base_dir=en_path.parent, lang="en", name=str(args.name or "")))
            except Exception as e:
                LOG.warning("Slide %s: failed to load English JSON (%s): %s", slide_name, en_path.name, e)
        else:
            LOG.info("Slide %s: English JSON missing (%s)", slide_name, en_path.name)

        if not blocks_all:
            shutil.copyfile(img_path, out_path)
            LOG.warning("Slide %s: no renderable blocks -> copy unchanged", slide_name)
            continue

        LOG.info("Slide %s/%s %s: rendering %d blocks (ar/en combined) onto %s",
                 idx, len(slide_names), slide_name, len(blocks_all), img_path.name)

        for bidx, block in enumerate(blocks_all, 1):
            LOG.info(
                "  block#%d lang=%s rect=(%d,%d,%d,%d) font=%s size=%s gf=%s align=%s",
                bidx,
                block.lang,
                block.x,
                block.y,
                block.width,
                block.height,
                (str(block.font_path) if block.font_path else "DEFAULT"),
                (str(block.font_size) if block.font_size else "DEFAULT"),
                f"{block.global_font:.3f}",
                block.align,
            )

            # Warn if box is out of bounds.
            bx1 = block.x + block.width
            by1 = block.y + block.height
            if block.x < 0 or block.y < 0 or bx1 > orig_size[0] or by1 > orig_size[1]:
                LOG.warning("  block#%d: box extends outside image bounds img=%s box_end=(%d,%d)", bidx, orig_size, bx1, by1)

            try:
                tile, (doc_w, doc_h) = _render_html_block_to_pil(
                    Qt,
                    QSizeF,
                    QImage,
                    QPainter,
                    QTextDocument,
                    QFontDatabase,
                    QFont,
                    block=block,
                )
            except Exception as e:
                LOG.warning("  block#%d: render failed: %s", bidx, e)
                continue

            # Overflow warnings (rendered document size vs the fixed box).
            if doc_w - float(block.width) > 1.0 or doc_h - float(block.height) > 1.0:
                LOG.warning(
                    "  block#%d: rendered content exceeds box: doc=(%.1f,%.1f) box=(%d,%d)",
                    bidx,
                    doc_w,
                    doc_h,
                    block.width,
                    block.height,
                )

            _paste_rgba(img, tile, x=block.x, y=block.y)

        # Save without changing dimensions.
        out_ext = out_path.suffix.lower()
        if out_ext in (".jpg", ".jpeg"):
            img.convert("RGB").save(out_path, format="JPEG", quality=95, subsampling=0)
        else:
            img.save(out_path, format="PNG")

        # Verify output size unchanged.
        try:
            out_size = Image.open(out_path).size
            if out_size != orig_size:
                LOG.warning("Slide %s: output size changed unexpectedly: %s -> %s", slide_name, orig_size, out_size)
        except Exception as e:
            LOG.warning("Slide %s: failed to verify output image size: %s", slide_name, e)

        LOG.info("Slide %s: saved -> %s", slide_name, out_path.name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
