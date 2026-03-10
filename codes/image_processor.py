# -*- coding: utf-8 -*-
"""
🖼️ Image Processor Module (API READY + CLI Compatible)

- Batch head-swap for all API images (attempt 1 only) + copy normal images
- OPTIONAL interactive CLI selection (ONLY when API_MODE=0)
- Text overlay (sequential or parallel)
- Resolution handling

IMPORTANT:
- Uses api_segmiod.py and its fixed signature:
    perform_head_swap(target_image_path, face_image_path, output_filename, face_url_cached=None)

API_MODE:
- If API_MODE=1 -> NO terminal input, NO interactive refine.
- If API_MODE=0 -> keep interactive CLI refine.
"""

import os
import cv2
import time
import shutil
from datetime import datetime
import uuid
from pathlib import Path


from config import HEAD_SWAP_DELAY
from api_segmiod import perform_head_swap, upload_to_segmind_storage
from text_handler import render_image
from utils import get_image_dimensions


# ---------------------------
# API Mode switch (HARD)
# ---------------------------
def _api_mode() -> bool:
    """
    HARD RULE:
    - Default = API mode ON (NO INPUT) unless API_MODE is explicitly set to 0.
    Why? Because any accidental stdin prompt will break the server.

    So:
      API_MODE=0  => CLI interactive allowed
      else        => API mode (no interactive)
    """
    v = os.getenv("API_MODE", "1")  # default ON
    return not (v.strip().lower() in ("0", "false", "no", "n"))


# ---------------------------
# General helpers
# ---------------------------
def resize_image_to_resolution(image, target_width, target_height):
    current_h, current_w = image.shape[:2]
    if current_w == target_width and current_h == target_height:
        return image
    # Deterministic, non-filtering resize to keep pixel mapping stable for text coordinates.
    return cv2.resize(image, (int(target_width), int(target_height)), interpolation=cv2.INTER_NEAREST)


def apply_resolution_to_images(images_dict, resolution_slides, use_parallel=None):
    """
    Uses resolution_slides from info.txt:
      [["slide_01", 2048, 2048], ["slide_02", 2048, 1024], ...]
    Returns a LIST of images resized in correct slide order.
    """
    if not images_dict:
        return []

    # order slides (be tolerant of unexpected keys)
    def _slide_num(k: str) -> int:
        try:
            return int(str(k).split("_")[1])
        except Exception:
            return 10**9

    slide_keys = sorted(images_dict.keys(), key=_slide_num)

    # Enforce strict, deterministic canvas sizes regardless of resolution_slides.
    REQUIRED_FIRST_LAST = (2048, 2048)
    REQUIRED_MIDDLE = (2048, 1024)
    first_key = slide_keys[0] if slide_keys else None
    last_key = slide_keys[-1] if slide_keys else None

    resized_images = []
    for slide_name in slide_keys:
        img = images_dict[slide_name]

        if first_key and slide_name == first_key:
            tw, th = REQUIRED_FIRST_LAST
        elif last_key and slide_name == last_key:
            tw, th = REQUIRED_FIRST_LAST
        else:
            tw, th = REQUIRED_MIDDLE

        if (img.shape[1], img.shape[0]) != (tw, th):
            img = cv2.resize(img, (int(tw), int(th)), interpolation=cv2.INTER_NEAREST)

        resized_images.append(img)

    return resized_images


def _safe_input(prompt: str) -> str:
    # Only used in CLI mode. In API mode this should never run.
    try:
        return input(prompt)
    except Exception:
        return ""


def _parse_slide_key(val: str) -> str | None:
    s = (val or "").strip().lower()
    if not s:
        return None

    if s.startswith("slide_"):
        tail = s.replace("slide_", "")
        if tail.isdigit():
            return f"slide_{int(tail):02d}"
        return s

    if s.isdigit():
        return f"slide_{int(s):02d}"

    return None


def _ensure_same_dims_as_original(scene_path: str, out_path: str) -> bool:
    original = cv2.imread(scene_path)
    if original is None:
        return False
    oh, ow = original.shape[:2]

    img = cv2.imread(out_path)
    if img is None:
        return False

    h, w = img.shape[:2]
    if (w, h) != (ow, oh):
        img = cv2.resize(img, (ow, oh), interpolation=cv2.INTER_CUBIC)
        cv2.imwrite(out_path, img)
    return True


def _scale_labels(labels, src_w, src_h, dst_w, dst_h):
    if not labels or src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        return labels

    sx = dst_w / float(src_w)
    sy = dst_h / float(src_h)
    sf = min(sx, sy)

    out = []
    for el in labels:
        e = dict(el)
        e["x"] = int(e.get("x", 0) * sx)
        e["y"] = int(e.get("y", 0) * sy)
        e["width"] = int(e.get("width", 0) * sx)
        e["height"] = int(e.get("height", 0) * sy)

        gf = e.get("global_font", 0)
        if gf and gf != 0:
            try:
                e["global_font"] = float(gf) * sf
            except Exception:
                pass

        out.append(e)
    return out


# ---------------------------
# Text overlay
# ---------------------------
def apply_text_to_images(
    images_dict,
    text_data,
    original_dims_dict,
    app,
    fonts_loaded,
    language,
    use_parallel=None,
    first_slide_font=None,
    rest_slides_font=None,
):
    from config import USE_PARALLEL_TEXT_PROCESSING

    if use_parallel is None:
        use_parallel = USE_PARALLEL_TEXT_PROCESSING

    if use_parallel and len(images_dict) > 1:
        return _apply_text_parallel(
            images_dict=images_dict,
            text_data=text_data,
            original_dims_dict=original_dims_dict,
            language=language,
            first_slide_font=first_slide_font,
            rest_slides_font=rest_slides_font,
        )

    return _apply_text_sequential(
    images_dict=images_dict,
    text_data=text_data,
    original_dims_dict=original_dims_dict,
    app=app,
    fonts_loaded=fonts_loaded,
    language=language,   # ✅ مهم

    )


def _apply_text_sequential(images_dict, text_data, original_dims_dict, app, fonts_loaded, language="en"):
    processed_images = {}

    for image_name, img in images_dict.items():
        current_h, current_w = img.shape[:2]



        if image_name not in text_data:
            processed_images[image_name] = img
            continue

        labels_list = text_data[image_name]



        first_key = list(text_data.keys())[0] if text_data else image_name
        is_first = (image_name == "slide_01" or image_name == first_key)

        img_with_text = render_image(
            image_name=image_name,
            text_data_list=labels_list,
            fonts_loaded=fonts_loaded,
            is_first_slide=is_first,
            image_data=img,
            language=language, 
            text_data_keys=list(text_data.keys())  # ✅ مهم عشان لو عربي يعمل flip جوّه text_handler
        )

        processed_images[image_name] = img_with_text if img_with_text is not None else img

    return processed_images


def _restore_image_worker(args):
    image_name, img, orig_w, orig_h = args
    current_h, current_w = img.shape[:2]
    if current_w != orig_w or current_h != orig_h:
        img = resize_image_to_resolution(img, orig_w, orig_h)
    return (image_name, img)


def _apply_text_parallel(images_dict, text_data, original_dims_dict, language, first_slide_font=None, rest_slides_font=None):
    from multiprocessing import Pool
    from config import MAX_TEXT_WORKERS, BASE_DIR
    from parallel_text_processor import apply_text_parallel

    restored_images = {}
    restore_tasks = []

    for image_name, img in images_dict.items():
        if image_name in original_dims_dict:
            orig_w, orig_h = original_dims_dict[image_name]
            restore_tasks.append((image_name, img, orig_w, orig_h))
        else:
            restored_images[image_name] = img

    if restore_tasks:
        workers = min(MAX_TEXT_WORKERS, len(restore_tasks))
        with Pool(processes=workers) as pool:
            results = pool.map(_restore_image_worker, restore_tasks)
        for image_name, img in results:
            restored_images[image_name] = img

    if first_slide_font and rest_slides_font:
        first_font_path = os.path.join(BASE_DIR, first_slide_font) if not os.path.isabs(first_slide_font) else first_slide_font
        rest_font_path = os.path.join(BASE_DIR, rest_slides_font) if not os.path.isabs(rest_slides_font) else rest_slides_font
    else:
        from config import EN_FIRST_SLIDE_FONT, EN_REST_SLIDES_FONT, AR_FIRST_SLIDE_FONT, AR_REST_SLIDES_FONT
        if language == "en":
            first_font_path = EN_FIRST_SLIDE_FONT
            rest_font_path = EN_REST_SLIDES_FONT
        else:
            first_font_path = AR_FIRST_SLIDE_FONT
            rest_font_path = AR_REST_SLIDES_FONT

    return apply_text_parallel(
        images_dict=restored_images,
        text_data=text_data,
        first_font_path=first_font_path,
        rest_font_path=rest_font_path,
        num_workers=MAX_TEXT_WORKERS,
    )


# ---------------------------
# Head swap helpers (NO ENV LEAK)
# ---------------------------
def _set_single_attempt_env(attempt_idx: int) -> None:
    os.environ["SEGMIND_SINGLE_ATTEMPT"] = "1"
    os.environ["SEGMIND_ATTEMPT_INDEX"] = str(int(attempt_idx))


def _clear_single_attempt_env() -> None:
    os.environ["SEGMIND_SINGLE_ATTEMPT"] = "0"
    os.environ.pop("SEGMIND_ATTEMPT_INDEX", None)


def _generate_single_attempt(
    scene_path: str,
    face_image_path: str,
    final_out_path: str,
    attempt_idx: int,
    face_url_cached: str | None = None,
) -> str | None:
    _set_single_attempt_env(attempt_idx)
    try:
        preview_path = perform_head_swap(
        target_image_path=scene_path,
        face_image_path=face_image_path,
        output_filename=final_out_path,
        face_url_cached=face_url_cached,   # ✅ Solution A
)
        if preview_path and os.path.exists(preview_path):
            _ensure_same_dims_as_original(scene_path, preview_path)
            return preview_path
        return None
    finally:
        _clear_single_attempt_env()


# ---------------------------
# CLI Interactive refine (ONLY when API_MODE=0)
# ---------------------------
def _slide_label_from_key(slide_key: str) -> str:
    if slide_key.startswith("slide_") and slide_key.replace("slide_", "").isdigit():
        return f"slide {int(slide_key.replace('slide_', '')):02d}"
    return slide_key.replace("_", " ")


def _try_label(slide_key: str, attempt_idx: int) -> str:
    base = _slide_label_from_key(slide_key)
    return f"{base}_try{attempt_idx}"


def _interactive_refine_before_pdf(api_map: dict, face_image_path: str):
    if not api_map:
        return

    slides = sorted(api_map.keys())

    while True:
        print("\nchoose any image you need to change? (n or 0 for exit)")
        for i, s in enumerate(slides, 1):
            print(f"{i}-{_slide_label_from_key(s)}")

        raw = _safe_input("input: ").strip()
        if raw.lower() in ("0", "q", "quit", "exit", "n", "no"):
            return

        slide_key = None
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(slides):
                slide_key = slides[idx - 1]
        if not slide_key:
            slide_key = _parse_slide_key(raw)

        if not slide_key or slide_key not in api_map:
            print("Invalid input.")
            continue

        scene_path = api_map[slide_key]["scene"]
        final_out = api_map[slide_key]["out"]

        tries: list[str] = []
        attempt = 1

        while True:
            print("regnerating photo ....")
            preview = _generate_single_attempt(scene_path, face_image_path, final_out, attempt)
            print("done")

            if preview:
                tries.append(preview)

            yn = _safe_input("do you like the result y/n?\n").strip().lower()
            if yn != "y":
                attempt += 1
                continue

            print("okay choose any result you want to save :")
            for i, _p in enumerate(tries, 1):
                print(f"{i}-{_try_label(slide_key, i)}")

            pick_raw = _safe_input("input:").strip()
            pick = int(pick_raw) if pick_raw.isdigit() else len(tries)
            if pick < 1 or pick > len(tries):
                pick = len(tries)

            chosen_path = tries[pick - 1]
            shutil.copyfile(chosen_path, final_out)
            _ensure_same_dims_as_original(scene_path, final_out)

            print(f"done saved {_slide_label_from_key(slide_key)}")
            break

        nxt = _safe_input("do you want to retry with another photo?\n").strip().lower()
        if nxt in ("0", "q", "quit", "exit", "n", "no"):
            return


# ---------------------------
# API helper: Regenerate a single slide (NO terminal)
# ---------------------------
def regenerate_single_slide(scene_path: str, face_image_path: str, final_out_path: str, attempts: int = 1) -> str | None:
    attempts = max(1, int(attempts))
    last_ok = None

    for attempt_idx in range(1, attempts + 1):
        prev = _generate_single_attempt(scene_path, face_image_path, final_out_path, attempt_idx)
        if prev and os.path.exists(prev):
            last_ok = prev

    if last_ok:
        shutil.copyfile(last_ok, final_out_path)
        _ensure_same_dims_as_original(scene_path, final_out_path)
        return final_out_path

    return None


# ---------------------------
# Main batch pipeline
# ---------------------------
def process_head_swap(clean_images_folder, character_image_path, character_name, story_folder, prompts_dict=None, use_parallel=None):
    head_swap_folder = os.path.join(story_folder, "Head_swap")
    os.makedirs(head_swap_folder, exist_ok=True)

    char_output_folder = os.path.join(head_swap_folder, character_name)
    os.makedirs(char_output_folder, exist_ok=True)

    api_images_folder = os.path.join(story_folder, "api_images")
    normal_images_folder = os.path.join(story_folder, "normal_images")

    api_images = [f for f in os.listdir(api_images_folder) if f.lower().endswith((".jpg", ".jpeg", ".png"))] if os.path.exists(api_images_folder) else []
    normal_images = [f for f in os.listdir(normal_images_folder) if f.lower().endswith((".jpg", ".jpeg", ".png"))] if os.path.exists(normal_images_folder) else []

    all_images = sorted(api_images + normal_images)
    if not all_images:
        return None, None
    # ✅ Solution A: upload face ONCE and reuse URL for all slides
    face_url_cached = upload_to_segmind_storage(character_image_path)

    if not face_url_cached:
        print("⚠️ [WARN] Face upload failed - will fallback to uploading per slide.")

    processed_images_dict = {}
    original_dims_dict = {}
    api_map = {}

    print(f"\n📊 Total images: {len(all_images)} | API: {len(api_images)} | Normal: {len(normal_images)}")

    for filename in all_images:
        name_no_ext = os.path.splitext(filename)[0]
        is_api = filename in api_images
        src_path = os.path.join(api_images_folder if is_api else normal_images_folder, filename)

        src_ext = Path(src_path).suffix.lower() or ".jpg"

        # 🔥 generate unique filename
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        unique_key = uuid.uuid4().hex[:6]

        # الاسم ثابت: slide_01.jpg
        out_path = os.path.join(char_output_folder, f"{name_no_ext}{src_ext}")


        dims = get_image_dimensions(src_path)
        if dims:
            orig_w, orig_h = dims
            if orig_w and orig_h:
                original_dims_dict[name_no_ext] = (orig_w, orig_h)

        if is_api:
            api_map[name_no_ext] = {"scene": src_path, "out": out_path}

        if os.path.exists(out_path):
            img = cv2.imread(out_path)
            if img is not None:
                processed_images_dict[name_no_ext] = img
            continue

        if not is_api:
            img = cv2.imread(src_path)
            if img is not None:
                cv2.imwrite(out_path, img)
                processed_images_dict[name_no_ext] = img
            continue

        print(f"\n🧩 Generating (batch attempt_1): {filename}")
        cand = _generate_single_attempt(
            src_path,
            character_image_path,
            out_path,
            1,
            face_url_cached=face_url_cached
)

        if cand and os.path.exists(cand):
            shutil.copyfile(cand, out_path)
            _ensure_same_dims_as_original(src_path, out_path)

        img = cv2.imread(out_path)
        if img is not None:
            processed_images_dict[name_no_ext] = img

        if HEAD_SWAP_DELAY and HEAD_SWAP_DELAY > 0:
            time.sleep(HEAD_SWAP_DELAY)

    # IMPORTANT: interactive refine is ONLY when API_MODE=0
    if api_map and (not _api_mode()):  # here _api_mode() True means API => skip interactive
        _interactive_refine_before_pdf(api_map=api_map, face_image_path=character_image_path)

        for slide_key, meta in api_map.items():
            outp = meta["out"]
            if os.path.exists(outp):
                img = cv2.imread(outp)
                if img is not None:
                    processed_images_dict[slide_key] = img

    return (processed_images_dict, original_dims_dict) if processed_images_dict else (None, None)
