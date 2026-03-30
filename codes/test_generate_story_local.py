# -*- coding: utf-8 -*-
"""
Local checks for story text pipeline (no HTTP server).

Usage (from repo root or codes/):
  set OPENAI_API_KEY=...   # optional, for AI branch test
  python codes/test_generate_story_local.py

Tests:
  1) Template path: read_text_data + validate_story_text_non_empty
  2) If OPENAI_API_KEY set: optional OpenAI generation on a tiny stub template
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# codes/ on path
CODES_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(CODES_DIR))

from text_handler import read_text_data, apply_name_placeholders_to_text_data  # noqa: E402
from story_ai import (  # noqa: E402
    validate_story_text_non_empty,
    generate_story_htmls_with_openai,
    merge_html_arrays,
    get_openai_api_key,
)


def test_template_story(story_root: Path, language: str, kid_name: str) -> None:
    translations = story_root / "Translations"
    if language == "en":
        text_file = translations / "en_text_data.txt"
    else:
        text_file = translations / "ar_text_data.txt"
        if not text_file.is_file():
            raise SystemExit(f"Missing {text_file.name}")

    print("### text_file:", text_file)
    data = read_text_data(str(text_file), user_name=kid_name, language=language)
    assert data, "read_text_data returned empty"
    plain = validate_story_text_non_empty(data)
    print("### OK template path, plain length:", len(plain))
    print("### excerpt:", plain[:240], "...")


def test_openai_stub() -> None:
    if not get_openai_api_key():
        print("### SKIP OpenAI test (OPENAI_API_KEY not set)")
        return

    template = {
        "slide_01": [
            {
                "html": "<html><body><p><span style='font-size:14px; color:#fff;'>[*Name*] went to the park.</span></p></body></html>",
                "global_font": 1.0,
                "x": 100,
                "y": 100,
                "width": 400,
                "height": 200,
            }
        ]
    }
    # Model rewrites html; merge back
    new_htmls = generate_story_htmls_with_openai(
        template,
        kid_name="Sam",
        language="en",
        story_title="Test",
        story_type="adventure",
        image_bytes=None,
    )
    merged = merge_html_arrays(template, new_htmls)
    merged = apply_name_placeholders_to_text_data(merged, "Sam", "en")
    plain = validate_story_text_non_empty(merged)
    print("### OK OpenAI stub path, plain length:", len(plain))
    print("### excerpt:", plain[:400])


def main() -> None:
    os.environ.setdefault("STORY_AI_DEBUG", "1")

    root = CODES_DIR.parent
    story = root / "Stories" / "Girls" / "Alia-and-the-Lost-Fairy-Wings-"
    if not story.is_dir():
        print("### Sample story folder missing:", story)
        sys.exit(1)

    print("=== 1) Template + name substitution ===")
    test_template_story(story, "en", "Alia")

    print("\n=== 2) Optional OpenAI ===")
    test_openai_stub()

    print("\n### All local checks finished.")


if __name__ == "__main__":
    main()
