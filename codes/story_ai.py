# -*- coding: utf-8 -*-
"""
AI-assisted story text generation for personalized PDF slides.

- Uses OpenAI chat completions (vision optional) to rewrite HTML labels while
  preserving layout fields (x, y, width, height, global_font) from the template.
- Template JSON comes from Translations/en_text_data.txt or ar_*.txt (same schema).

Environment:
  OPENAI_API_KEY   (required for AI path)
  OPENAI_BASE_URL  (optional, e.g. Azure OpenAI)
  OPENAI_MODEL     (default: gpt-4o)
  STORY_AI_DEBUG=1 (verbose logging)
"""

from __future__ import annotations

import base64
import copy
import html as html_lib
import json
import os
import re
from typing import Any

DEBUG = os.environ.get("STORY_AI_DEBUG", "0").strip().lower() in ("1", "true", "yes", "y")


def _dlog(msg: str) -> None:
    if DEBUG:
        print(f"[story_ai] {msg}", flush=True)


def get_openai_api_key() -> str:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return key


def get_openai_model() -> str:
    return (os.environ.get("OPENAI_MODEL") or "gpt-4o").strip()


def get_openai_base_url() -> str | None:
    u = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    return u or None


def plain_text_from_text_data(text_data: dict[str, Any]) -> str:
    """Flatten all label HTML to plain text for length checks and API responses."""
    parts: list[str] = []
    for _slide, labels in sorted(text_data.items(), key=lambda x: x[0]):
        if not isinstance(labels, list):
            continue
        for lab in labels:
            if not isinstance(lab, dict):
                continue
            h = lab.get("html") or ""
            plain = re.sub(r"<[^>]+>", " ", h)
            plain = html_lib.unescape(plain)
            plain = re.sub(r"\s+", " ", plain).strip()
            if plain:
                parts.append(plain)
    return " ".join(parts).strip()


def extract_html_arrays(text_data: dict[str, Any]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for slide, labels in text_data.items():
        if not isinstance(labels, list):
            continue
        out[str(slide)] = []
        for lab in labels:
            if isinstance(lab, dict) and "html" in lab:
                out[str(slide)].append(str(lab.get("html") or ""))
    return out


def merge_html_arrays(template: dict[str, Any], new_htmls: dict[str, list[str]]) -> dict[str, Any]:
    merged = copy.deepcopy(template)
    for slide, labels in merged.items():
        if slide not in new_htmls or not isinstance(labels, list):
            continue
        nh = new_htmls[slide]
        for i, lab in enumerate(labels):
            if isinstance(lab, dict) and i < len(nh):
                lab["html"] = nh[i]
    return merged


def _build_openai_client():
    try:
        from openai import OpenAI
    except ModuleNotFoundError as e:
        raise RuntimeError("The 'openai' package is required for AI story generation. Install: pip install openai") from e

    key = get_openai_api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    kwargs: dict[str, Any] = {"api_key": key}
    base = get_openai_base_url()
    if base:
        kwargs["base_url"] = base
    return OpenAI(**kwargs)


def _image_mime_from_bytes(data: bytes) -> str:
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 6 and (data[:6] in (b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "image/jpeg"


def generate_story_htmls_with_openai(
    template_text_data: dict[str, Any],
    kid_name: str,
    language: str,
    story_title: str,
    story_type: str,
    image_bytes: bytes | None,
) -> dict[str, list[str]]:
    """
    Returns mapping slide_XX -> list of HTML strings (same order/length as template).
    """
    key = get_openai_api_key()
    if not key:
        raise RuntimeError("OPENAI_API_KEY is missing; cannot generate AI story text.")

    html_arrays = extract_html_arrays(template_text_data)
    if not html_arrays:
        raise RuntimeError("Template has no html labels to generate.")

    compact_template = json.dumps(html_arrays, ensure_ascii=False)
    _dlog(f"template html keys: {list(html_arrays.keys())}")
    print(
        "### [STORY_AI] kid_name=%r language=%r story_title=%r story_type=%r image_bytes=%s"
        % (kid_name, language, story_title, story_type, f"{len(image_bytes)} B" if image_bytes else "None"),
        flush=True,
    )

    sys_prompt = (
        "You personalize children's storybook slide text. "
        "You MUST respond with a single JSON object only (no markdown fences). "
        "Keys are slide ids like slide_01, slide_02. Each value is a JSON array of HTML strings. "
        "The number of strings per slide MUST match the input exactly. "
        "Preserve inline CSS from the template (font-size, colors, font-weight, alignment). "
        "Rewrite narrative text only; keep structure similar. "
        "Use placeholders [*NAME*] and [*Name*] for English, or [*الاسم*] for Arabic where appropriate."
    )

    user_text = f"""Story metadata:
- Child name: {kid_name}
- Language code: {language}  (write story text in this language)
- Story title: {story_title or "(use template theme)"}
- Story type / theme: {story_type or "(infer from template)"}

Template (JSON of slide -> html fragments in order):
{compact_template}

Rewrite every html fragment with a fresh, coherent personalized story. Keep each fragment suitable for its text box."""

    client = _build_openai_client()
    model = get_openai_model()

    user_content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    if image_bytes:
        mime = _image_mime_from_bytes(image_bytes)
        b64 = base64.standard_b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        user_content.append({"type": "image_url", "image_url": {"url": data_url}})
        print("### [STORY_AI] attached kid_image to OpenAI request (vision)", flush=True)
    else:
        print("### [STORY_AI] no kid_image bytes; text-only request", flush=True)

    print("### [STORY_AI] prompt (user message, text part) excerpt:\n", user_text[:2500], "\n...", flush=True)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
    except Exception as e:
        print("### [STORY_AI] OpenAI request FAILED:", repr(e), flush=True)
        raise RuntimeError(f"OpenAI chat completion failed: {e}") from e

    raw = (resp.choices[0].message.content or "").strip()
    print("### [STORY_AI] model raw response (first 2000 chars):\n", raw[:2000], flush=True)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print("### [STORY_AI] JSON parse error:", repr(e), flush=True)
        raise RuntimeError(f"Model returned invalid JSON: {e}") from e

    if not isinstance(parsed, dict):
        raise RuntimeError("Model JSON root must be an object.")

    # Accept either flat slide_* keys or {"slides": {...}}
    if "slides" in parsed and isinstance(parsed["slides"], dict):
        parsed = parsed["slides"]

    # Validate counts
    for slide, frags in html_arrays.items():
        if slide not in parsed:
            raise RuntimeError(f"Model output missing slide key: {slide}")
        got = parsed[slide]
        if not isinstance(got, list) or len(got) != len(frags):
            raise RuntimeError(
                f"Slide {slide}: expected {len(frags)} html fragments, got {type(got).__name__}/{len(got) if isinstance(got, list) else 'n/a'}"
            )
        for i, h in enumerate(got):
            if not isinstance(h, str) or len(h.strip()) < 1:
                raise RuntimeError(f"Slide {slide} fragment {i} is empty or not a string.")

    return {str(k): [str(x) for x in v] for k, v in parsed.items() if str(k).startswith("slide_")}


def validate_story_text_non_empty(text_data: dict[str, Any], min_plain_len: int = 5) -> str:
    plain = plain_text_from_text_data(text_data)
    if len(plain.strip()) < min_plain_len:
        raise RuntimeError(
            f"Generated story text is too short (plain len={len(plain.strip())}, min={min_plain_len})."
        )
    return plain
