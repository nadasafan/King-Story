# -*- coding: utf-8 -*-
"""
PDF story pipeline: slide file selection + translation I/O logging.

- Slide images: after many regenerations, the best pixel source is usually the
  latest slide_XX_tryN. If no try files exist, the base slide_XX.* is used.
- Translations: read_text_data always reads from disk (no cache). Helpers here
  add structured logging for frontend vs Swagger parity debugging.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2


def is_try_stem(stem: str) -> bool:
    return re.search(r"_try\d+$", stem, flags=re.IGNORECASE) is not None


def base_slide_from_stem(stem: str) -> str:
    return re.sub(r"_try\d+$", "", stem, flags=re.IGNORECASE)


def pick_slide_file_for_pdf(files: list[Path]) -> Path | None:
    """
    Choose one file per logical slide group.

    Policy (matches long retry sessions):
    - If any slide_XX_tryN exists, use the file with the **largest** N (latest retry).
    - Otherwise use the base slide_XX.ext (non-try).
    """
    if not files:
        return None
    try_entries: list[tuple[int, Path]] = []
    base_candidates: list[Path] = []
    for p in files:
        s = p.stem
        if is_try_stem(s):
            m = re.search(r"_try(\d+)$", s, flags=re.IGNORECASE)
            n = int(m.group(1)) if m else 0
            try_entries.append((n, p))
        else:
            base_candidates.append(p)
    if try_entries:
        try_entries.sort(key=lambda x: -x[0])
        return try_entries[0][1]
    if base_candidates:
        return sorted(base_candidates, key=lambda x: x.name)[0]
    return None


def load_slide_bgr_images_for_pdf(img_dir: Path) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """
    Build mapping slide_XX -> BGR ndarray (OpenCV).

    Returns:
        images_dict, all_file_stems, sources_meta (slide -> chosen filename + tag)
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    all_stems: list[str] = []
    for f in sorted(img_dir.iterdir()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            continue
        all_stems.append(f.stem)
        base = base_slide_from_stem(f.stem)
        groups[base].append(f)

    images_dict: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for base in sorted(groups.keys()):
        files = groups[base]
        chosen = pick_slide_file_for_pdf(files)
        if chosen is None:
            continue
        img = cv2.imread(str(chosen))
        if img is None:
            print(f"### [PDF_IMG] WARN unreadable file skipped: {chosen}", flush=True)
            continue
        images_dict[base] = img
        if is_try_stem(chosen.stem):
            m = re.search(r"_try(\d+)$", chosen.stem, flags=re.IGNORECASE)
            tag = f"latest_try#{m.group(1)}" if m else "try"
        else:
            tag = "base"
        sources[base] = f"{chosen.name} [{tag}]"

    return images_dict, all_stems, sources


def log_translation_file_event(text_file: Path, phase: str = "read") -> None:
    """Log translation file access (size + mtime) for audit trail."""
    try:
        st = text_file.stat()
        print(
            f"### [TRANSLATIONS] {phase} path={text_file} "
            f"size_bytes={st.st_size} mtime={int(st.st_mtime)}",
            flush=True,
        )
    except OSError as e:
        print(f"### [TRANSLATIONS] {phase} FAILED stat {text_file}: {e}", flush=True)


def warn_pdf_order_missing(
    ordered_names: list[str],
    images_with_text: dict[str, Any],
) -> None:
    """Log slides listed in info.txt order but missing after text render."""
    missing = [n for n in ordered_names if n not in images_with_text]
    if missing:
        print(
            "### [PDF_ORDER] WARN resolution_slides order references slides not in rendered map:",
            missing,
            flush=True,
        )
