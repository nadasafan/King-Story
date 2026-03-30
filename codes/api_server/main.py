# codes/api_server/main.py
# -*- coding: utf-8 -*-

import os
import re
import uuid
import shutil
import json
import base64
import copy
from pathlib import Path
from urllib.parse import quote

import cv2
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse

# ---- import your existing pipeline modules (from codes folder) ----
import sys
THIS_FILE = Path(__file__).resolve()
CODES_DIR = THIS_FILE.parents[1]          # .../codes
BASE_DIR = THIS_FILE.parents[2]           # project root (contains Stories, Fonts, TempUploads, ...)
sys.path.insert(0, str(CODES_DIR))

from utils import crop_face_only, read_info_file, get_image_dimensions, parse_story_info_json_content
from api_segmiod import perform_head_swap
from image_processor import process_head_swap, apply_text_to_images, scale_text_data_to_native_sizes
from pdf_generator import create_pdf_from_images
from text_handler import (
    load_custom_fonts,
    read_text_data,
    apply_name_placeholders_to_text_data,
)
from story_ai import (
    generate_story_htmls_with_openai,
    merge_html_arrays,
    validate_story_text_non_empty,
    get_openai_api_key,
    log_text_image_coverage,
    MIN_STORY_TEXT_PLAIN_LEN,
    assert_pdf_sequence_has_renderable_text,
)
from config import PDF_PRESERVE_NATIVE_IMAGE_SIZE, PDF_TEXT_SCALE_MODE, PDF_PIL_DPI
from pdf_story_pipeline import (
    load_slide_bgr_images_for_pdf,
    log_translation_file_event,
    warn_pdf_order_missing,
    base_slide_from_stem,
)

print("### LOADED API SERVER FROM:", __file__, flush=True)

# Important for headless servers / windows service
os.environ.setdefault("QT_QPA_PLATFORM", os.getenv("QT_QPA_PLATFORM", "offscreen"))

app = FastAPI(title="Stories Studio API", version="1.0.0")
# =========================
# ENABLE CORS FOR FRONTEND ACCESS
# =========================
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # اسمحي لكل الدومينات تستخدم API
    allow_credentials=True,
    allow_methods=["*"],        # GET / POST / DELETE / OPTIONS
    allow_headers=["*"],        # كل الهيدرز مسموحة
)
# ---- folders ----
STORIES_DIR = BASE_DIR / "Stories"
TEMP_UPLOADS_DIR = BASE_DIR / "TempUploads"
RESULT_DIR = BASE_DIR / "result"

TEMP_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# Helpers: security + paths
# =========================

def _normalize_gender(g: str) -> str:
    g = (g or "").strip().lower()
    if g in ("male", "boy", "m"):
        return "male"
    if g in ("female", "girl", "f"):
        return "female"
    raise HTTPException(status_code=400, detail="Invalid gender. Use 'male' or 'female'.")


def _story_folder(gender: str, story_code: str) -> Path:
    gender = _normalize_gender(gender)
    story_code = (story_code or "").strip()
    if not story_code:
        raise HTTPException(status_code=400, detail="story_code is required.")

    sub = "Boys" if gender == "male" else "Girls"
    p = STORIES_DIR / sub / story_code
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=404, detail=f"Story folder not found: {p}")
    return p


def _is_within_base(path: Path) -> bool:
    try:
        path.resolve().relative_to(BASE_DIR.resolve())
        return True
    except Exception:
        return False


def _guard_path_inside_base(path_str: str) -> Path:
    if not path_str:
        raise HTTPException(status_code=400, detail="path is required.")
    p = Path(path_str)
    try:
        p = p.resolve()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid path.")

    if not _is_within_base(p):
        raise HTTPException(status_code=403, detail="Path is outside BASE_DIR.")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return p


def _safe_filename(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return str(uuid.uuid4()) + ".png"
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name)
    return name


def _to_file_url(abs_path: Path) -> str:
    return f"/file?path={quote(str(abs_path), safe='')}"


def _client_facing_base_url(request: Request) -> str:
    """
    Base URL as the browser should use (supports reverse proxies).
    If X-Forwarded-* are missing, falls back to request.base_url.
    """
    xf_proto = (request.headers.get("x-forwarded-proto") or "").strip().split(",")[0].strip()
    xf_host = (request.headers.get("x-forwarded-host") or "").strip().split(",")[0].strip()
    if xf_host and xf_proto:
        return f"{xf_proto}://{xf_host}".rstrip("/")
    if xf_host:
        scheme = xf_proto or (request.url.scheme or "https")
        return f"{scheme}://{xf_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _pdf_urls_for_response(request: Request, pdf_out_path: Path) -> tuple[str, str]:
    cb = uuid.uuid4().hex[:12]
    rel = _to_file_url(pdf_out_path) + f"&cb={cb}"
    return rel, _client_facing_base_url(request) + rel


def _save_upload(upload: UploadFile, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    fname = _safe_filename(upload.filename)
    out = dst_dir / f"{uuid.uuid4().hex}_{fname}"
    with open(out, "wb") as f:
        shutil.copyfileobj(upload.file, f)
    return out


def _find_story_root_from_any_path(p: Path) -> Path | None:
    for parent in [p] + list(p.parents):
        if (parent / "api_images").exists() and (parent / "Translations").exists():
            return parent
    return None


def _find_scene_image(story_root: Path, slide_stem: str) -> Path | None:
    api_dir = story_root / "api_images"
    for ext in (".jpg", ".jpeg", ".png"):
        cand = api_dir / f"{slide_stem}{ext}"
        if cand.exists():
            return cand
    if api_dir.exists():
        for f in api_dir.iterdir():
            if f.is_file() and f.stem == slide_stem and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                return f
    return None


def _next_try_index(folder: Path, slide_stem: str, ext: str) -> int:
    pat = re.compile(rf"^{re.escape(slide_stem)}_try(\d+){re.escape(ext)}$", re.IGNORECASE)
    mx = 0
    if folder.exists():
        for f in folder.iterdir():
            if not f.is_file():
                continue
            m = pat.match(f.name)
            if m:
                try:
                    mx = max(mx, int(m.group(1)))
                except Exception:
                    pass
    return mx + 1


def _set_single_attempt_env(attempt_idx: int) -> None:
    os.environ["SEGMIND_INTERACTIVE"] = "0"
    os.environ["SEGMIND_SINGLE_ATTEMPT"] = "1"
    os.environ["SEGMIND_ATTEMPT_INDEX"] = str(int(attempt_idx))


def _clear_single_attempt_env() -> None:
    os.environ["SEGMIND_SINGLE_ATTEMPT"] = "0"
    os.environ.pop("SEGMIND_ATTEMPT_INDEX", None)


def _truthy_form(s: str) -> bool:
    return (s or "").strip().lower() in ("1", "true", "yes", "y", "on")


# =========================
# info.txt loader (per story)
# =========================

def _read_story_info_json(story_root: Path) -> dict:
    """
    Reads story_root/info.txt (JSON). Missing file → {}.
    Invalid JSON → HTTP 422 so PDF is not silently built with wrong text placement.
    """
    info_path = story_root / "info.txt"
    if not info_path.exists():
        print("### [INFO] info.txt not found at:", info_path, flush=True)
        return {}

    try:
        raw = info_path.read_text(encoding="utf-8")
        data = parse_story_info_json_content(raw)
        if not data.get("resolution_slides"):
            print(
                "### [INFO] loaded info.txt but resolution_slides empty/missing:",
                info_path,
                flush=True,
            )
        else:
            print("### [INFO] loaded info.txt:", info_path, flush=True)
        return data
    except json.JSONDecodeError as e:
        print("### [INFO] failed to parse info.txt:", info_path, "reason:", repr(e), flush=True)
        raise HTTPException(
            status_code=422,
            detail=(
                f"ملف info.txt تالف (JSON غير صالح): {e}. "
                f"غالباً علامة اقتباس زائدة داخل مسار خط مثل AR_REST_SLIDE_FONT. "
                f"Invalid info.txt JSON — often a stray quote inside a font path. Path: {info_path}"
            ),
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        print("### [INFO] failed to read info.txt:", info_path, "reason:", repr(e), flush=True)
        raise HTTPException(
            status_code=422,
            detail=f"Cannot read info.txt at {info_path}: {e}",
        ) from e


def _resize_to_resolution_map(images_dict: dict, resolution_slides: list) -> dict:
    """
    resolution_slides format:
      [["slide_01", 1024, 1024], ["slide_03", 1448, 720], ...]
    Returns dict keyed by slide_name with resized images.
    """
    if not resolution_slides:
        return images_dict

    out = {}
    for item in resolution_slides:
        try:
            name, w, h = item
            name = str(name)
            w = int(w); h = int(h)
        except Exception:
            continue

        if name not in images_dict:
            continue

        img = images_dict[name]
        # upscale/downscale wisely
        interp = cv2.INTER_AREA if (w < img.shape[1] or h < img.shape[0]) else cv2.INTER_LANCZOS4
        resized = cv2.resize(img, (w, h), interpolation=interp)
        out[name] = resized

    for k, v in images_dict.items():
        if k not in out:
            out[k] = v

    return out


# =========================
# 0) root ping (debug)
# =========================
@app.get("/")
def root():
    print("### HIT / FROM:", __file__, flush=True)
    return {"status": "ok", "file": str(THIS_FILE)}


# =========================
# 1) GET /file
# =========================
@app.get("/file")
def get_file(path: str):
    print("### HIT /file FROM:", __file__, flush=True)
    p = _guard_path_inside_base(path)
    if p.suffix.lower() == ".pdf":
        return FileResponse(
            str(p),
            media_type="application/pdf",
            filename=p.name,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
            },
        )
    return FileResponse(str(p))


# =========================
# 2) POST /delete-file
# =========================
@app.post("/delete-file")
def delete_file(path: str = Form(...)):
    print("### HIT /delete-file FROM:", __file__, flush=True)
    p = _guard_path_inside_base(path)

    if "Head_swap" not in [x.name for x in p.parents] and p.parent.name != "Head_swap":
        raise HTTPException(status_code=403, detail="Delete allowed only inside Head_swap.")

    try:
        p.unlink()
        return {"status": "success", "message": "File deleted", "path": str(p)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {e}")


# =========================
# 2.5) POST /confirm-slide  ✅ NEW
# =========================
@app.post("/confirm-slide")
def confirm_slide(
    chosen_slide_path: str = Form(...),
):
    """
    Choose a *_tryN image (or any slide image) as FINAL:
    - copies chosen file over the base slide name (slide_XX.ext)
    - PDF generation otherwise picks the latest slide_XX_tryN per slide when try files exist
    """
    print("### HIT /confirm-slide FROM:", __file__, flush=True)
    chosen_p = _guard_path_inside_base(chosen_slide_path)
    if not chosen_p.is_file():
        raise HTTPException(status_code=400, detail="chosen_slide_path must be a file.")

    # must live inside Head_swap folder (safety)
    if "Head_swap" not in [x.name for x in chosen_p.parents] and chosen_p.parent.name != "Head_swap":
        raise HTTPException(status_code=403, detail="confirm allowed only inside Head_swap.")

    base_stem = base_slide_from_stem(chosen_p.stem)  # slide_03_try2 -> slide_03
    final_p = chosen_p.parent / f"{base_stem}{chosen_p.suffix}"

    try:
        shutil.copyfile(chosen_p, final_p)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to confirm slide: {e}")

    return {
        "status": "success",
        "chosen_slide_path": str(chosen_p.resolve()),
        "chosen_slide_url": _to_file_url(chosen_p.resolve()),
        "final_slide_path": str(final_p.resolve()),
        "final_slide_url": _to_file_url(final_p.resolve()),
        "base_slide_stem": base_stem,
        "note": "Copied to base filename. PDF pipeline prefers latest *_tryN pixels per slide; base file is used when no tries exist.",
    }


# =========================
# 3) POST /head-swap
# =========================
@app.post("/head-swap")
async def head_swap(
    gender: str = Form(...),
    story_code: str = Form(...),
    character_image: UploadFile | None = File(None),
):
    print("### HIT /head-swap FROM:", __file__, flush=True)

    story_root = _story_folder(gender, story_code)

    if character_image is None:
        raise HTTPException(status_code=400, detail="character_image is required for head-swap.")

    raw_path = _save_upload(character_image, TEMP_UPLOADS_DIR)

    cropped_path = TEMP_UPLOADS_DIR / f"cropped_{raw_path.name}"
    face_path = crop_face_only(str(raw_path), str(cropped_path), padding=2) or str(raw_path)
    face_path = Path(face_path)

    character_name = face_path.stem

    os.environ["SEGMIND_INTERACTIVE"] = "0"
    os.environ["SEGMIND_SINGLE_ATTEMPT"] = "1"
    os.environ["SEGMIND_ATTEMPT_INDEX"] = "1"
    os.environ["API_MODE"] = "1"

    processed_images_dict, _original_dims_dict = process_head_swap(
        clean_images_folder=None,
        character_image_path=str(face_path),
        character_name=character_name,
        story_folder=str(story_root),
        prompts_dict=None,
        use_parallel=None,
    )

    if not processed_images_dict:
        raise HTTPException(status_code=500, detail="Head swap failed or produced no images.")

    image_folder = story_root / "Head_swap" / character_name
    if not image_folder.exists():
        hs = story_root / "Head_swap"
        if hs.exists():
            subs = [d for d in hs.iterdir() if d.is_dir()]
            subs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            if subs:
                image_folder = subs[0]

    images = []
    for f in sorted(image_folder.glob("*")):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            images.append({
                "name": f.name,
                "path": str(f.resolve()),
                "url": _to_file_url(f.resolve()),
            })

    print("### head-swap output folder:", image_folder, flush=True)
    print("### head-swap images:", len(images), flush=True)

    return {
        "status": "success",
        "gender": _normalize_gender(gender),
        "story_code": story_code,
        "image_folder": str(image_folder.resolve()),
        "images": images,
    }


# =========================
# 4) GET /head-swap/list
# =========================
@app.get("/head-swap/list")
def head_swap_list(gender: str, story_code: str, session: str):
    print("### HIT /head-swap/list FROM:", __file__, flush=True)
    story_root = _story_folder(gender, story_code)

    image_folder = story_root / "Head_swap" / session
    if not image_folder.exists() or not image_folder.is_dir():
        raise HTTPException(status_code=404, detail="Session folder not found.")

    images = []
    for f in sorted(image_folder.glob("*")):
        if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png"):
            images.append({
                "name": f.name,
                "path": str(f.resolve()),
                "url": _to_file_url(f.resolve()),
            })

    return {
        "status": "success",
        "gender": _normalize_gender(gender),
        "story_code": story_code,
        "session": session,
        "image_folder": str(image_folder.resolve()),
        "images": images,
    }


# =========================
# 5) POST /regenerate-slide
# =========================
@app.post("/regenerate-slide")
async def regenerate_slide(
    slide_path: str = Form(...),
    face_image: UploadFile | None = File(None),
):
    print("### HIT /regenerate-slide FROM:", __file__, flush=True)

    slide_p = _guard_path_inside_base(slide_path)
    print(f"### [RETRY] path={slide_p} stem={slide_p.stem}", flush=True)

    if face_image is None:
        folder = slide_p.parent
        stem = slide_p.stem
        stem_base = re.sub(r"_try\d+$", "", stem, flags=re.IGNORECASE)
        ext = slide_p.suffix.lower() or ".jpg"
        try_index = _next_try_index(folder, stem_base, ext)
        new_p = folder / f"{stem_base}_try{try_index}{ext}"
        shutil.copyfile(slide_p, new_p)
        print(f"### [RETRY] duplicate_only stem_base={stem_base} try_index={try_index} -> {new_p.name}", flush=True)

        return {
            "status": "success",
            "try_index": try_index,
            "old_slide_path": str(slide_p.resolve()),
            "old_slide_url": _to_file_url(slide_p.resolve()),
            "new_slide_path": str(new_p.resolve()),
            "new_slide_url": _to_file_url(new_p.resolve()),
            "mode": "duplicate_only",
        }

    raw_face = _save_upload(face_image, TEMP_UPLOADS_DIR)
    cropped = TEMP_UPLOADS_DIR / f"cropped_{raw_face.name}"
    face_path = crop_face_only(str(raw_face), str(cropped), padding=2) or str(raw_face)
    face_path = Path(face_path)

    story_root = _find_story_root_from_any_path(slide_p)
    if story_root is None:
        raise HTTPException(status_code=400, detail="Could not infer story root from slide_path.")

    stem = slide_p.stem
    stem_base = re.sub(r"_try\d+$", "", stem, flags=re.IGNORECASE)
    scene_path = _find_scene_image(story_root, stem_base)
    if scene_path is None:
        raise HTTPException(status_code=404, detail=f"Original scene not found for {stem_base} under api_images.")

    final_slide = slide_p.parent / f"{stem_base}{slide_p.suffix}"
    ext = final_slide.suffix.lower() or ".jpg"
    try_index = _next_try_index(final_slide.parent, stem_base, ext)
    print(
        f"### [RETRY] ai_regenerate story_root={story_root} stem_base={stem_base} "
        f"try_index={try_index} scene={scene_path.name}",
        flush=True,
    )

    _set_single_attempt_env(try_index)
    try:
        preview_path = perform_head_swap(
            target_image_path=str(scene_path),
            face_image_path=str(face_path),
            output_filename=str(final_slide),
            face_url_cached=None,
        )
    finally:
        _clear_single_attempt_env()

    if not preview_path or not Path(preview_path).exists():
        raise HTTPException(status_code=500, detail="Regenerate failed (no preview produced).")

# ✅ FIX: force retry image to same size as original scene
    try:
        orig = cv2.imread(str(scene_path))
        new_img = cv2.imread(str(preview_path))

        if orig is not None and new_img is not None:
            oh, ow = orig.shape[:2]
            nh, nw = new_img.shape[:2]

            if (ow, oh) != (nw, nh):
                interp = cv2.INTER_AREA if (ow < nw or oh < nh) else cv2.INTER_LANCZOS4
                new_img = cv2.resize(new_img, (ow, oh), interpolation=interp)
                cv2.imwrite(str(preview_path), new_img)
                print(f"### [RETRY RESIZE] fixed retry image size from {(nw, nh)} to {(ow, oh)}", flush=True)
    except Exception as e:
        print(f"### [RETRY RESIZE] skipped due to error: {e}", flush=True)

    new_p = Path(preview_path).resolve()

    return {
        "status": "success",
        "try_index": try_index,
        "old_slide_path": str(slide_p.resolve()),
        "old_slide_url": _to_file_url(slide_p.resolve()),
        "new_slide_path": str(new_p),
        "new_slide_url": _to_file_url(new_p),
        "mode": "ai_regenerate",
    }


# =========================
# 6) POST /generate-story/pdf  (RESIZE -> TEXT -> PDF)
# =========================
@app.post("/generate-story/pdf")
def generate_story_pdf(
    request: Request,
    language: str = Form(""),
    user_name: str = Form(...),
    images_folder: str = Form(...),
    story_title: str = Form(""),
    story_type: str = Form(""),
    use_ai_story: str = Form("false"),
    kid_image_base64: str = Form(""),
    kid_image: UploadFile | None = File(None),
):
    print("\n### HIT /generate-story/pdf FROM:", __file__, flush=True)
    print(
        "### [CLIENT] generate-story/pdf (Swagger and browser multipart use the same handler; "
        "frontend must send multipart/form-data with language, user_name, images_folder).",
        flush=True,
    )
    print("### INPUT language:", language, "user_name (kid_name):", user_name, flush=True)
    print("### INPUT images_folder:", images_folder, flush=True)
    print(
        "### INPUT story_title:", story_title,
        "story_type:", story_type,
        "use_ai_story:", use_ai_story,
        "kid_image present:", bool(kid_image and getattr(kid_image, "filename", None)),
        "kid_image_base64 len:", len((kid_image_base64 or "").strip()),
        flush=True,
    )

    language = (language or "").strip().lower()
    if not language:
        raise HTTPException(
            status_code=400,
            detail=(
                "language is empty (required: en or ar). / اللغة فاضية — يجب إرسال ar أو en"
            ),
        )
    if language not in ("en", "ar"):
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid language: use 'en' or 'ar' only. / لغة غير صالحة: المسموح ar أو en فقط"
            ),
        )
    if not user_name.strip():
        raise HTTPException(status_code=400, detail="user_name is required.")

    img_dir = _guard_path_inside_base(images_folder)
    if not img_dir.is_dir():
        raise HTTPException(status_code=400, detail="images_folder must be a directory.")

    story_root = _find_story_root_from_any_path(img_dir)
    if story_root is None:
        raise HTTPException(status_code=400, detail="Could not infer story root from images_folder.")

    print("### story_root:", story_root, flush=True)
    print(
        "### [PDF_OPTS] preserve_native=",
        PDF_PRESERVE_NATIVE_IMAGE_SIZE,
        "text_scale=",
        PDF_TEXT_SCALE_MODE,
        "pil_dpi=",
        PDF_PIL_DPI,
        flush=True,
    )

    # Retry endpoints toggle SEGMIND_* env; PDF generation must not inherit stale attempt state.
    _clear_single_attempt_env()
    print("### [PDF] cleared SEGMIND single-attempt env (independent of retry count)", flush=True)

    # 1) read story info.json FROM story_root/info.txt (the one you mentioned)
    story_info = _read_story_info_json(story_root)
    res_map_list = story_info.get("resolution_slides") or []

    # also read your existing helper (keeps name templates etc.)
    (
        en_story_name,
        ar_story_name,
        _unused_resolution_slides,
        first_slide_font,
        rest_slides_font,
        ar_first_slide_font,
        ar_rest_slides_font,
    ) = read_info_file(str(story_root))

    pdf_name_tpl = (en_story_name or "Story_EN") if language == "en" else (ar_story_name or "Story_AR")

    # 2) One image per slide_XX: use LATEST slide_XX_tryN when tries exist (matches 1000+ retries), else base file
    images_dict, all_stems, pdf_image_sources = load_slide_bgr_images_for_pdf(img_dir)

    print("### images on disk (all stems):", len(all_stems), flush=True)
    print("### usable slides (latest try or base per slide):", len(images_dict), flush=True)
    print("### [PDF_IMG] per-slide source:", json.dumps(pdf_image_sources, ensure_ascii=False)[:4000], flush=True)

    if not images_dict:
        raise HTTPException(
            status_code=400,
            detail="No readable slide images in images_folder (need slide_XX.* or slide_XX_tryN.*).",
        )

    # 3) Design canvas vs native pixels for PDF:
    #    - PDF_PRESERVE_NATIVE_IMAGE_SIZE=1 (default): keep head-swap pixel sizes → PDF page sizes;
    #      scale label geometry from resolution_slides → native using scale_text_data_to_native_sizes.
    #    - PDF_PRESERVE_NATIVE_IMAGE_SIZE=0: resize images to resolution_slides first (matches old behavior).
    if PDF_PRESERVE_NATIVE_IMAGE_SIZE:
        print(
            "### [RESIZE] skipped (PDF_PRESERVE_NATIVE_IMAGE_SIZE: keep native slide sizes for PDF pages; "
            "text coords scaled from design when resolution_slides present)",
            flush=True,
        )
        if not res_map_list:
            print(
                "### [WARN] no resolution_slides in info.txt — overlay uses raw coords "
                "(may misalign if image size ≠ translation design)",
                flush=True,
            )
    elif res_map_list:
        images_dict = _resize_to_resolution_map(images_dict, res_map_list)
        print("### [RESIZE] applied info.txt resolution_slides ✅", flush=True)
    else:
        print("### [RESIZE] skipped (no resolution_slides found in info.txt)", flush=True)

    # MUST be taken AFTER resize: text_handler uses this to avoid re-scaling away from design resolution.
    # If this was captured before resize, text was drawn at wrong coords → invisible text on PDF (response still had story_text).
    original_dims_dict = {k: (v.shape[1], v.shape[0]) for k, v in images_dict.items()}
    print("### [DIMS] canvas for text overlay (post-resize, w,h):", list(original_dims_dict.items())[:5], flush=True)

    # 4) choose text file + fonts
    # Story text lives only under story_root/Translations/ on disk — frontend sends language (en|ar) only.
    # en → en_text_data.txt, ar → ar_text_data.txt (same layout as repo Stories/.../Translations/).
    translations_folder = story_root / "Translations"
    if not translations_folder.exists():
        raise HTTPException(status_code=404, detail="Translations folder not found.")

    if language == "en":
        text_file = translations_folder / "en_text_data.txt"
        selected_first_font = first_slide_font
        selected_rest_font = rest_slides_font
    else:
        text_file = translations_folder / "ar_text_data.txt"
        selected_first_font = ar_first_slide_font
        selected_rest_font = ar_rest_slides_font

    if not text_file.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Translation text file missing for language={language}: "
                f"expected {text_file.name} under {translations_folder}"
            ),
        )

    print("### text_file:", text_file, flush=True)
    print("### fonts config first/rest:", selected_first_font, "|", selected_rest_font, flush=True)

    # 5) load story text (translation template and/or OpenAI), validate, render onto slides
    text_render_ok = False
    images_with_text = images_dict
    story_text = ""

    try:
        print("### [TEXT] building text_data ...", flush=True)

        if _truthy_form(use_ai_story):
            if not get_openai_api_key():
                raise HTTPException(
                    status_code=400,
                    detail="use_ai_story is true but OPENAI_API_KEY is not set. Set the environment variable or pass use_ai_story=false.",
                )
            log_translation_file_event(text_file, phase="ai_template_read")
            template_raw = read_text_data(str(text_file), user_name="", language=language)
            if not template_raw:
                raise RuntimeError("read_text_data returned None/empty (template for AI path).")

            image_bytes = None
            if kid_image is not None and getattr(kid_image, "filename", None):
                image_bytes = kid_image.file.read()
                print("### [STORY] kid_image upload size bytes:", len(image_bytes or b""), flush=True)
            elif (kid_image_base64 or "").strip():
                raw_b64 = kid_image_base64.strip()
                if raw_b64.startswith("data:"):
                    raw_b64 = raw_b64.split(",", 1)[1]
                try:
                    image_bytes = base64.b64decode(raw_b64)
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Invalid kid_image_base64: {e}") from e
                print("### [STORY] kid_image_base64 decoded bytes:", len(image_bytes), flush=True)

            new_htmls = generate_story_htmls_with_openai(
                template_raw,
                kid_name=user_name.strip(),
                language=language,
                story_title=(story_title or "").strip(),
                story_type=(story_type or "").strip(),
                image_bytes=image_bytes,
            )
            text_data = merge_html_arrays(template_raw, new_htmls)
            text_data = apply_name_placeholders_to_text_data(text_data, user_name.strip(), language)
        else:
            log_translation_file_event(text_file, phase="template_read")
            text_data = read_text_data(str(text_file), user_name=user_name, language=language)
            if not text_data:
                raise RuntimeError("read_text_data returned None/empty")

        text_data = copy.deepcopy(text_data)

        # إعادة قراءة ملف الترجمة من القرص قبل الـ PDF (افتراضي: مفعّل) — يثبّت النص بعد head swap أو ريتراي كثير
        # تعطيل: ضع STORY_TEXT_RELOAD_BEFORE_PDF=0
        _skip_tr_reload = os.environ.get("STORY_TEXT_RELOAD_BEFORE_PDF", "1").strip().lower() in (
            "0",
            "false",
            "no",
            "off",
        )
        if not _skip_tr_reload and not _truthy_form(use_ai_story):
            log_translation_file_event(text_file, phase="reload_before_pdf")
            reloaded = read_text_data(str(text_file), user_name=user_name.strip(), language=language)
            if not reloaded:
                raise RuntimeError(
                    "فشل إعادة قراءة ملف الترجمة قبل PDF. / STORY_TEXT_RELOAD_BEFORE_PDF: re-read failed"
                )
            text_data = copy.deepcopy(reloaded)
            print("### [TRANSLATIONS] reload_before_pdf applied (fresh read from disk)", flush=True)

        log_text_image_coverage(text_data, images_dict)

        text_data_for_render = text_data
        if PDF_PRESERVE_NATIVE_IMAGE_SIZE and res_map_list:
            text_data_for_render = scale_text_data_to_native_sizes(text_data, images_dict, res_map_list)

        story_text = validate_story_text_non_empty(text_data, min_plain_len=MIN_STORY_TEXT_PLAIN_LEN)
        print("### [STORY] final story_text plain length:", len(story_text), flush=True)
        print("### [STORY] final story_text excerpt:", story_text[:500], flush=True)

        print("### [TEXT] load_custom_fonts ...", flush=True)
        fonts_loaded = load_custom_fonts(
            language=language,
            first_slide_font_path=selected_first_font,
            rest_slides_font_path=selected_rest_font,
            base_dir=str(BASE_DIR),
        )
        print("### [TEXT] fonts_loaded:", fonts_loaded, flush=True)

        print("### [TEXT] apply_text_to_images (sequential) ...", flush=True)
        images_with_text = apply_text_to_images(
            images_dict=images_dict,
            text_data=text_data_for_render,
            original_dims_dict=original_dims_dict,
            app=None,
            fonts_loaded=fonts_loaded,
            language=language,
            use_parallel=False,
            first_slide_font=selected_first_font,
            rest_slides_font=selected_rest_font,
        )

        if not images_with_text:
            raise RuntimeError("apply_text_to_images returned None/empty")

        if res_map_list:
            ordered_for_pdf_text = [str(x[0]) for x in res_map_list]
        else:
            ordered_for_pdf_text = sorted(images_with_text.keys())

        assert_pdf_sequence_has_renderable_text(text_data_for_render, images_with_text, ordered_for_pdf_text)

        text_render_ok = True
        print("### [TEXT] SUCCESS ✅ (overlay verified for PDF sequence)", flush=True)

    except HTTPException:
        raise
    except Exception as e:
        print("### [TEXT] FAILED ❌ reason:", repr(e), flush=True)
        msg = str(e)
        detail = (
            f"{msg} | فشل توليد النص أو رسمه على الشرائح؛ لن يُنشأ PDF بدون نص."
            if "لا يمكن إنشاء PDF" in msg or "Cannot create PDF" in msg
            else f"Story text generation or rendering failed (no PDF produced): {msg}"
        )
        raise HTTPException(status_code=500, detail=detail) from e

    # 6) order slides for PDF
    if res_map_list:
        ordered_names = [str(x[0]) for x in res_map_list]
        warn_pdf_order_missing(ordered_names, images_with_text)
        final_images = [images_with_text[n] for n in ordered_names if n in images_with_text]
    else:
        final_images = [images_with_text[name] for name in sorted(images_with_text.keys())]

    print("### final_images count:", len(final_images), flush=True)

    if not final_images:
        raise HTTPException(status_code=500, detail="No final images to build PDF.")

    # 7) build pdf filename
    pdf_filename = pdf_name_tpl
    if language == "en":
        pdf_filename = (
            pdf_filename.replace("Name", user_name)
            .replace("name", user_name)
            .replace("NAME", user_name.upper())
        )
    else:
        pdf_filename = (
            pdf_filename.replace("الاسم", user_name)
            .replace("اسم", user_name)
        )
    pdf_filename = f"{pdf_filename}.pdf"

    pdf_path = (RESULT_DIR / pdf_filename).resolve()
    print("### pdf_path:", pdf_path, flush=True)

    # Hard gate: story_text must stay non-empty after all image/text steps (no silent blank PDF)
    print(
        "### [PDF_GATE] pre-build story_text_len=",
        len(story_text),
        "min_required=",
        MIN_STORY_TEXT_PLAIN_LEN,
        flush=True,
    )
    if len(story_text.strip()) < MIN_STORY_TEXT_PLAIN_LEN:
        raise HTTPException(
            status_code=500,
            detail=f"story_text too short ({len(story_text.strip())} chars) before PDF build; minimum {MIN_STORY_TEXT_PLAIN_LEN}.",
        )

    # 8) create pdf
    try:
        pdf_out_path = create_pdf_from_images(
            final_images,
            str(pdf_path),
            use_parallel=False,
            story_text=story_text,
            min_story_text_len=MIN_STORY_TEXT_PLAIN_LEN,
        )
    except TypeError:
        pdf_out_path = create_pdf_from_images(
            final_images,
            str(pdf_path),
            use_parallel=None,
            story_text=story_text,
            min_story_text_len=MIN_STORY_TEXT_PLAIN_LEN,
        )

    if not pdf_out_path:
        raise HTTPException(status_code=500, detail="Failed to generate PDF.")

    pdf_out_path = Path(pdf_out_path).resolve()

    if not pdf_out_path.exists():
        raise HTTPException(status_code=500, detail=f"PDF not found after generation: {pdf_out_path}")

    print("### PDF OK ✅", flush=True)

    pdf_url, pdf_absolute_url = _pdf_urls_for_response(request, pdf_out_path)
    print(
        "### [PDF] client_base=",
        _client_facing_base_url(request),
        "pdf_url_len=",
        len(pdf_url),
        flush=True,
    )

    return {
        "status": "success",
        "language": language,
        "user_name": user_name,
        "kid_name": user_name,
        "story_title": (story_title or "").strip(),
        "story_type": (story_type or "").strip(),
        "use_ai_story": _truthy_form(use_ai_story),
        # Full plain-text story for frontend (same field Swagger and browser clients get)
        "story_text": story_text,
        "final_story_text": story_text,
        "story_text_length": len(story_text),
        "min_story_text_length_required": MIN_STORY_TEXT_PLAIN_LEN,
        "images_folder": str(img_dir.resolve()),
        "pdf_path": str(pdf_out_path),
        # Relative /file URL + unique cb= to defeat browser/CDN caches of an old blank PDF
        "pdf_url": pdf_url,
        # Open this in the browser (correct host behind nginx; avoids wrong baseUrl on the client)
        "pdf_absolute_url": pdf_absolute_url,
        "text_rendered": text_render_ok,
        "note": "Story text validated, rendered on slides, and passed to PDF builder." if text_render_ok else "Unexpected: text_rendered false after success path.",
    }