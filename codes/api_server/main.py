# codes/api_server/main.py
# -*- coding: utf-8 -*-

import os
import re
import uuid
import shutil
import json
from pathlib import Path
from urllib.parse import quote

import cv2
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse

# ---- import your existing pipeline modules (from codes folder) ----
import sys
THIS_FILE = Path(__file__).resolve()
CODES_DIR = THIS_FILE.parents[1]          # .../codes
BASE_DIR = THIS_FILE.parents[2]           # project root (contains Stories, Fonts, TempUploads, ...)
sys.path.insert(0, str(CODES_DIR))

from utils import crop_face_only, read_info_file, get_image_dimensions
from api_segmiod import perform_head_swap
from image_processor import process_head_swap, apply_text_to_images
from pdf_generator import create_pdf_from_images
from text_handler import load_custom_fonts, read_text_data

print("### LOADED API SERVER FROM:", __file__, flush=True)

# Important for headless servers / windows service
os.environ.setdefault("QT_QPA_PLATFORM", os.getenv("QT_QPA_PLATFORM", "offscreen"))

app = FastAPI(title="Stories Studio API", version="1.0.0")

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


def _is_try_image(stem: str) -> bool:
    return re.search(r"_try\d+$", stem, flags=re.IGNORECASE) is not None


# =========================
# info.txt loader (per story)
# =========================

def _read_story_info_json(story_root: Path) -> dict:
    """
    Reads story_root/info.txt which is JSON in your project.
    Falls back to {} if missing/invalid.
    """
    info_path = story_root / "info.txt"
    if not info_path.exists():
        print("### [INFO] info.txt not found at:", info_path, flush=True)
        return {}

    try:
        raw = info_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        print("### [INFO] loaded info.txt:", info_path, flush=True)
        return data
    except Exception as e:
        print("### [INFO] failed to parse info.txt:", info_path, "reason:", repr(e), flush=True)
        return {}


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
        resized = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
        out[name] = resized

    for k, v in images_dict.items():
        if k not in out:
            out[k] = v

    return out


def _base_slide_from_stem(stem: str) -> str:
    """
    slide_03_try2 -> slide_03
    slide_03      -> slide_03
    """
    return re.sub(r"_try\d+$", "", stem, flags=re.IGNORECASE)


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
    - PDF generator will automatically use it because it skips *_tryN
    """
    print("### HIT /confirm-slide FROM:", __file__, flush=True)
    chosen_p = _guard_path_inside_base(chosen_slide_path)
    if not chosen_p.is_file():
        raise HTTPException(status_code=400, detail="chosen_slide_path must be a file.")

    # must live inside Head_swap folder (safety)
    if "Head_swap" not in [x.name for x in chosen_p.parents] and chosen_p.parent.name != "Head_swap":
        raise HTTPException(status_code=403, detail="confirm allowed only inside Head_swap.")

    base_stem = _base_slide_from_stem(chosen_p.stem)  # slide_03_try2 -> slide_03
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
        "note": "Confirmed. PDF will use final_slide_path (base name) not the _try image.",
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

    if face_image is None:
        folder = slide_p.parent
        stem = slide_p.stem
        stem_base = re.sub(r"_try\d+$", "", stem, flags=re.IGNORECASE)
        ext = slide_p.suffix.lower() or ".jpg"
        try_index = _next_try_index(folder, stem_base, ext)
        new_p = folder / f"{stem_base}_try{try_index}{ext}"
        shutil.copyfile(slide_p, new_p)

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
    language: str = Form(...),
    user_name: str = Form(...),
    images_folder: str = Form(...),
):
    print("\n### HIT /generate-story/pdf FROM:", __file__, flush=True)
    print("### INPUT language:", language, "user_name:", user_name, flush=True)
    print("### INPUT images_folder:", images_folder, flush=True)

    language = (language or "").strip().lower()
    if language not in ("en", "ar"):
        raise HTTPException(status_code=400, detail="language must be 'en' or 'ar'.")
    if not user_name.strip():
        raise HTTPException(status_code=400, detail="user_name is required.")

    img_dir = _guard_path_inside_base(images_folder)
    if not img_dir.is_dir():
        raise HTTPException(status_code=400, detail="images_folder must be a directory.")

    story_root = _find_story_root_from_any_path(img_dir)
    if story_root is None:
        raise HTTPException(status_code=400, detail="Could not infer story root from images_folder.")

    print("### story_root:", story_root, flush=True)

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

    # 2) load images (skip _try)
    images_dict = {}
    all_stems = []

    for f in sorted(img_dir.iterdir()):
        if not (f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")):
            continue

        all_stems.append(f.stem)
        if _is_try_image(f.stem):
            continue

        img = cv2.imread(str(f))
        if img is None:
            continue

        images_dict[f.stem] = img

    print("### images on disk:", len(all_stems), flush=True)
    print("### usable images (no _try):", len(images_dict), flush=True)

    if not images_dict:
        raise HTTPException(status_code=400, detail="No usable images found (after skipping _tryN).")

    # 3) Resize FIRST using story_root/info.txt resolution_slides
    if res_map_list:
        images_dict = _resize_to_resolution_map(images_dict, res_map_list)
        print("### [RESIZE] applied info.txt resolution_slides ✅", flush=True)
    else:
        print("### [RESIZE] skipped (no resolution_slides found in info.txt)", flush=True)

    # 4) choose text file + fonts
    translations_folder = story_root / "Translations"
    if not translations_folder.exists():
        raise HTTPException(status_code=404, detail="Translations folder not found.")

    if language == "en":
        text_file = translations_folder / "en_text_data.txt"
        selected_first_font = first_slide_font
        selected_rest_font = rest_slides_font
    else:
        ar_files = sorted([p for p in translations_folder.iterdir() if p.is_file() and p.name.startswith("ar_")])
        if not ar_files:
            raise HTTPException(status_code=404, detail="No Arabic translation file found (ar_*.txt).")
        text_file = ar_files[0]
        selected_first_font = ar_first_slide_font
        selected_rest_font = ar_rest_slides_font

    print("### text_file:", text_file, flush=True)
    print("### fonts config first/rest:", selected_first_font, "|", selected_rest_font, flush=True)

    # 5) render text (NO original_dims_dict => prevents double-scaling)
    text_render_ok = False
    images_with_text = images_dict

    try:
        print("### [TEXT] parsing text data ...", flush=True)
        text_data = read_text_data(str(text_file), user_name=user_name, language=language)
        if not text_data:
            raise RuntimeError("read_text_data returned None/empty")

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
            text_data=text_data,
            original_dims_dict={},   # IMPORTANT: prevent extra scaling
            app=None,
            fonts_loaded=fonts_loaded,
            language=language,
            use_parallel=False,
            first_slide_font=selected_first_font,
            rest_slides_font=selected_rest_font,
        )

        if not images_with_text:
            raise RuntimeError("apply_text_to_images returned None/empty")

        text_render_ok = True
        print("### [TEXT] SUCCESS ✅", flush=True)

    except Exception as e:
        print("### [TEXT] FAILED ❌ reason:", repr(e), flush=True)
        print("### [TEXT] FALLBACK SAFE MODE (no text).", flush=True)
        images_with_text = images_dict
        text_render_ok = False

    # 6) order slides for PDF
    if res_map_list:
        ordered_names = [str(x[0]) for x in res_map_list]
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

    # 8) create pdf
    try:
        ok = create_pdf_from_images(final_images, str(pdf_path), use_parallel=False)
    except TypeError:
        ok = create_pdf_from_images(final_images, str(pdf_path), use_parallel=None)

    if not ok or not pdf_path.exists():
        raise HTTPException(status_code=500, detail="Failed to generate PDF.")

    print("### PDF OK ✅", flush=True)

    return {
        "status": "success",
        "language": language,
        "user_name": user_name,
        "images_folder": str(img_dir.resolve()),
        "pdf_path": str(pdf_path),
        "pdf_url": _to_file_url(pdf_path),
        "text_rendered": text_render_ok,
        "note": "Text rendered successfully." if text_render_ok else "Fallback SAFE MODE: PDF generated without text rendering.",
    }