# -*- coding: utf-8 -*-
"""
🌐 Segmind - FaceSwap Only (API-READY)

✅ Same signature:
   perform_head_swap(target_image_path, face_image_path, output_filename, face_url_cached=None)

Behavior (API):
- NO interactive terminal input (no yes/no).
- NO JSON caching (always re-upload).
- Supports:
  - Single attempt mode (SEGMIND_SINGLE_ATTEMPT=1 + SEGMIND_ATTEMPT_INDEX=N)
  - Otherwise: runs finite attempts (SEGMIND_MAX_ATTEMPTS, default=1) and saves last successful as final.

Notes:
- Keeps "tryN" previews for debugging, and always copies the last successful to output_filename.
"""

import os
import time
import base64
import shutil
import requests
import cv2
import numpy as np
# تفعيل split halves
os.environ.setdefault("SEGMIND_SPLIT_HALVES", "1")

from config import (
    SEGMIND_API_KEY,
    SEGMIND_TIMEOUT,
    SEGMIND_SEED,
)

SEGMIND_FACESWAP_V5_URL = "https://api.segmind.com/v1/faceswap-v5"

# ---------------------------
# HTTP session (keep-alive) + in-memory upload cache  ✅ Solution A
# ---------------------------
# Keep one Session per process to reuse TCP/TLS connections -> faster
_SESSION = requests.Session()

# Cache Segmind Storage URLs for identical local files (path + mtime + size)
_UPLOAD_URL_CACHE = {}
_MAX_UPLOAD_CACHE = int(os.getenv("SEGMIND_UPLOAD_CACHE_MAX", "256") or "256")


def _file_fingerprint(p: str):
    try:
        st = os.stat(p)
        return (os.path.abspath(p), int(st.st_mtime_ns), int(st.st_size))
    except Exception:
        return None


def upload_to_segmind_storage(image_path: str, retries: int = 3, wait_sec: int = 2) -> str | None:
    """
    ✅ Solution A helper:
    Upload file once then reuse the same URL for the same file (within same worker/process).
    """
    fp = _file_fingerprint(image_path)
    if fp and fp in _UPLOAD_URL_CACHE:
        return _UPLOAD_URL_CACHE[fp]

    url = _upload_to_segmind_storage(image_path, retries=retries, wait_sec=wait_sec)
    if url and fp:
        if len(_UPLOAD_URL_CACHE) >= _MAX_UPLOAD_CACHE:
            _UPLOAD_URL_CACHE.pop(next(iter(_UPLOAD_URL_CACHE)))  # simple eviction
        _UPLOAD_URL_CACHE[fp] = url
    return url


# segmind sdk reads key from env -> set once
if SEGMIND_API_KEY:
    os.environ.setdefault("SEGMIND_API_KEY", SEGMIND_API_KEY)


# ---------------------------
# Logging helper
# ---------------------------
def _verbose() -> bool:
    return os.getenv("SEGMIND_VERBOSE", "1").strip().lower() in ("1", "true", "yes", "y")


def _log(msg: str) -> None:
    if _verbose():
        print(msg)


# ---------------------------
# Helpers (env)
# ---------------------------
def _is_single_attempt_from_env() -> bool:
    return os.getenv("SEGMIND_SINGLE_ATTEMPT", "0").strip().lower() in ("1", "true", "yes", "y")


def _attempt_index_from_env() -> int:
    v = (os.getenv("SEGMIND_ATTEMPT_INDEX", "") or "").strip()
    if v.isdigit():
        return max(1, int(v))
    return 1


def _max_attempts_from_env(default: int = 1) -> int:
    v = (os.getenv("SEGMIND_MAX_ATTEMPTS", "") or "").strip()
    if v.isdigit():
        return max(1, int(v))
    return default


def _seed_base() -> int:
    try:
        return int(SEGMIND_SEED) if SEGMIND_SEED is not None else 42
    except Exception:
        return 42


# ---------------------------
# Segmind storage upload (ALWAYS upload)
# ---------------------------
def _upload_to_segmind_storage(image_path: str, retries: int = 3, wait_sec: int = 2) -> str | None:
    if not os.path.exists(image_path):
        _log(f"   ❌ File not found: {image_path}")
        return None

    try:
        import segmind  # pip install segmind
    except Exception as e:
        _log("   ❌ segmind package not installed. Run: pip install segmind")
        _log(f"   📄 Import error: {e}")
        return None

    for attempt in range(1, retries + 1):
        try:
            _log(f"   ⬆️  Upload attempt {attempt}/{retries}: {os.path.basename(image_path)}")
            result = segmind.files.upload(image_path)
            urls = (result or {}).get("file_urls") or []
            if urls:
                return urls[0]
            _log("   ❌ Segmind upload returned no file_urls")
        except Exception as e:
            _log(f"   ❌ Segmind upload error: {e}")
            if attempt < retries:
                time.sleep(wait_sec)
            else:
                return None

    return None


# ---------------------------
# Save Segmind response to file
# ---------------------------
def _save_response_to_file(resp: requests.Response, output_path: str, timeout: int) -> bool:
    ctype = (resp.headers.get("Content-Type") or "").lower()
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # direct image bytes
    if "image/" in ctype:
        with open(output_path, "wb") as f:
            f.write(resp.content)
        return True

    # otherwise JSON
    try:
        j = resp.json()
    except Exception:
        _log("   ❌ Response is not image and not JSON. Cannot save output.")
        try:
            _log(resp.text[:800])
        except Exception:
            pass
        return False

    out_url = (
        j.get("output_url")
        or j.get("image_url")
        or j.get("url")
        or (j.get("data") or {}).get("url")
        or (j.get("data") or {}).get("output_url")
        or (j.get("data") or {}).get("image_url")
    )

    if out_url:
        try:
            r2 = requests.get(out_url, timeout=timeout)
            if r2.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(r2.content)
                return True
        except Exception as e:
            _log(f"   ❌ Failed to download output_url: {e}")

    b64 = (
        j.get("image_base64")
        or (j.get("data") or {}).get("image_base64")
        or (j.get("data") or {}).get("base64")
        or j.get("base64")
    )

    if b64:
        try:
            raw = base64.b64decode(b64)
            with open(output_path, "wb") as f:
                f.write(raw)
            return True
        except Exception as e:
            _log(f"   ❌ Failed to decode base64 output: {e}")

    _log("   ❌ JSON response did not include a usable output.")
    _log(str(j)[:800])
    return False


# ---------------------------
# API Call (faceswap-v5 only)
# ---------------------------
def _call_faceswap_v5(target_url: str, face_url: str, seed: int, timeout: int) -> requests.Response:
    headers = {"x-api-key": SEGMIND_API_KEY, "Content-Type": "application/json"}
    data = {
        "target_image": target_url,
        "source_image": face_url,
        "seed": int(seed),
        "image_format": "png",
        "quality": 95,
    }
    return _SESSION.post(SEGMIND_FACESWAP_V5_URL, headers=headers, json=data, timeout=timeout)



def _multi_face_enabled() -> bool:
    # default ON
    return os.getenv("SEGMIND_MULTI_FACE", "1").strip().lower() in ("1", "true", "yes", "y")

def _split_two_halves_enabled() -> bool:
    # default ON
    return os.getenv("SEGMIND_SPLIT_HALVES", "1").strip().lower() in ("1", "true", "yes", "y")

def _is_wide_split_image(bgr_img) -> bool:
    # default ratio 1.6 زي اللي عندك
    ratio = float(os.getenv("SEGMIND_SPLIT_RATIO", "1.6"))
    h, w = bgr_img.shape[:2]
    return w >= int(h * ratio)

def _detect_faces_opencv(bgr_img):

    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)

    frontal = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    profile = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_profileface.xml"
    )

    # ---------- Strict detection ----------
    faces_f = frontal.detectMultiScale(gray, 1.08, 4)
    faces_p = profile.detectMultiScale(gray, 1.08, 3)

    gray_flip = cv2.flip(gray, 1)
    faces_pf = profile.detectMultiScale(gray_flip, 1.08, 3)

    # ---------- fallback detection ----------
    if (len(faces_f) + len(faces_p) + len(faces_pf)) < 1:
        faces_f = frontal.detectMultiScale(gray, 1.05, 3)
        faces_p = profile.detectMultiScale(gray, 1.05, 3)
        faces_pf = profile.detectMultiScale(gray_flip, 1.05, 3)

    # ---------- filter faces ----------
    h, w = bgr_img.shape[:2]

    def _filter_faces(faces):
        out = []
        for (x, y, fw, fh) in faces:

            area = fw * fh
            img_area = w * h

            if fw < 40 or fh < 40:
                continue

            r = area / float(img_area)
            if r < 0.01 or r > 0.60:
                continue

            ar = fw / float(fh)
            if ar < 0.6 or ar > 1.6:
                continue

            out.append((x, y, fw, fh))

        return out

    faces_f = _filter_faces(faces_f)
    faces_p = _filter_faces(faces_p)
    faces_pf = _filter_faces(faces_pf)

    bboxes = []

    def add_faces(faces, flipped=False):

        for (x, y, fw, fh) in faces:

            if flipped:
                x = w - (x + fw)

            pad_x = int(0.45 * fw)
            pad_y = int(0.65 * fh)

            x1 = max(0, x - pad_x)
            y1 = max(0, y - pad_y)
            x2 = min(w, x + fw + pad_x)
            y2 = min(h, y + fh + int(0.20 * fh))

            bboxes.append((x1, y1, x2, y2))

    add_faces(faces_f)
    add_faces(faces_p)
    add_faces(faces_pf, True)

    bboxes.sort(key=lambda b: b[0])

    return bboxes


def _mean_abs_diff(a, b):
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    return float(np.mean(np.abs(a - b)))


# ---------------------------
# Main
# ---------------------------
def _perform_head_swap_single(
    target_image_path: str,
    face_image_path: str,
    output_filename: str,
    face_url_cached: str | None = None,
):
    """
    DO NOT change signature.

    Returns:
      - In normal mode: final output_filename path if saved, else None
      - In single attempt mode: preview path (NOT final), or None
    """
    try:
        # snapshot env-based behavior ONCE per call (safer under server)
        single_attempt = _is_single_attempt_from_env()
        attempt_idx = _attempt_index_from_env()
        max_attempts = _max_attempts_from_env(default=1)

        if not os.path.exists(target_image_path):
            _log(f"   ❌ Target not found: {target_image_path}")
            return None
        if not os.path.exists(face_image_path):
            _log(f"   ❌ Face not found: {face_image_path}")
            return None

        base_name, base_ext = os.path.splitext(output_filename)
        if not base_ext:
            base_ext = ".png"

        seed_base = _seed_base()

        # ALWAYS upload target
        _log("   ☁️  Uploading target to Segmind Storage...")
        target_url = upload_to_segmind_storage(target_image_path)
        if not target_url:
            _log("   ❌ Failed to upload target")
            return None

        # face url: either cached passed in, or upload every time
        if face_url_cached:
            face_url = face_url_cached
        else:
            _log("   ☁️  Uploading face to Segmind Storage...")
            face_url = upload_to_segmind_storage(face_image_path)
            if not face_url:
                _log("   ❌ Failed to upload face")
                return None

        # SINGLE ATTEMPT MODE
        if single_attempt:
            preview_path = f"{base_name}_try{attempt_idx}{base_ext}"
            attempt_seed = seed_base + (attempt_idx - 1)

            _log(f"\n🚀 Single Attempt {attempt_idx} (faceswap-v5)")
            resp = _call_faceswap_v5(target_url, face_url, attempt_seed, SEGMIND_TIMEOUT)

            if resp.status_code != 200:
                _log(f"   ❌ Segmind faceswap-v5 error: {resp.status_code}")
                try:
                    _log(resp.text[:800])
                except Exception:
                    pass
                return None

            if not _save_response_to_file(resp, preview_path, SEGMIND_TIMEOUT):
                _log("   ❌ Could not save preview.")
                return None

            _log(f"✅ Single attempt preview saved: {preview_path}")
            return preview_path

        # NORMAL MODE (FINITE)
        max_attempts = max(1, int(max_attempts))
        last_success_preview = None

        for n in range(1, max_attempts + 1):
            preview_path = f"{base_name}_try{n}{base_ext}"
            attempt_seed = seed_base + (n - 1)

            _log(f"\n🚀 Attempt {n}/{max_attempts} (faceswap-v5)")
            resp = _call_faceswap_v5(target_url, face_url, attempt_seed, SEGMIND_TIMEOUT)

            if resp.status_code != 200:
                _log(f"   ❌ Segmind faceswap-v5 error: {resp.status_code}")
                try:
                    _log(resp.text[:800])
                except Exception:
                    pass
                continue

            if not _save_response_to_file(resp, preview_path, SEGMIND_TIMEOUT):
                _log("   ❌ Could not save faceswap preview.")
                continue

            last_success_preview = preview_path

        if last_success_preview and os.path.exists(last_success_preview):
            os.makedirs(os.path.dirname(output_filename) or ".", exist_ok=True)
            shutil.copyfile(last_success_preview, output_filename)
            _log(f"\n✅ Saved last successful preview as final: {output_filename}")
            return output_filename

        _log("\n❌ No successful output produced.")
        return None

    except requests.exceptions.Timeout:
        _log("   ❌ Request timeout")
        return None
    except Exception as e:
        _log(f"   ❌ Exception: {e}")
        return None
def _perform_split_halves_swap(
    base_bgr,
    face_image_path: str,
    output_filename: str,
    face_url_cached: str | None = None,
):
    """
    تعمل faceswap للـ left half والـ right half كل واحد لوحده
    وبعدين تدمجهم في output_filename
    """

    base_name, base_ext = os.path.splitext(output_filename)
    if not base_ext:
        base_ext = ".png"

    h, w = base_bgr.shape[:2]
    mid = w // 2

    left = base_bgr[:, :mid].copy()
    right = base_bgr[:, mid:].copy()

    tmp_left_in = f"{base_name}_halfL_IN.png"
    tmp_right_in = f"{base_name}_halfR_IN.png"
    tmp_left_out = f"{base_name}_halfL_OUT.png"
    tmp_right_out = f"{base_name}_halfR_OUT.png"

    cv2.imwrite(tmp_left_in, left)
    cv2.imwrite(tmp_right_in, right)

    # ✅ ارفع face مرة واحدة (نفس فكرة api_client) :contentReference[oaicite:1]{index=1}
    if not face_url_cached:
        _log("   ☁️  Uploading face once (cache for halves)...")
        face_url_cached = upload_to_segmind_storage(face_image_path)
        if not face_url_cached:
            _log("   ❌ Failed to upload face for split halves.")
            return None

    _log("   🧩 Split-halves: swapping LEFT half...")
    outL = _perform_head_swap_single(tmp_left_in, face_image_path, tmp_left_out, face_url_cached)

    _log("   🧩 Split-halves: swapping RIGHT half...")
    outR = _perform_head_swap_single(tmp_right_in, face_image_path, tmp_right_out, face_url_cached)

    if not outL or not outR or (not os.path.exists(outL)) or (not os.path.exists(outR)):
        _log("   ❌ Split-halves: one of halves failed. Fallback should happen.")
        return None

    left_swapped = cv2.imread(outL)
    right_swapped = cv2.imread(outR)

    if left_swapped is None or right_swapped is None:
        _log("   ❌ Split-halves: could not read swapped halves.")
        return None

    # تأكد المقاسات
    if left_swapped.shape[0] != h:
        left_swapped = cv2.resize(left_swapped, (mid, h))
    if right_swapped.shape[0] != h:
        right_swapped = cv2.resize(right_swapped, (w - mid, h))

    if left_swapped.shape[1] != mid:
        left_swapped = cv2.resize(left_swapped, (mid, h))
    if right_swapped.shape[1] != (w - mid):
        right_swapped = cv2.resize(right_swapped, (w - mid, h))

    composed = np.concatenate([left_swapped, right_swapped], axis=1)

    os.makedirs(os.path.dirname(output_filename) or ".", exist_ok=True)
    cv2.imwrite(output_filename, composed)
    _log(f"   ✅ Saved split-halves final: {output_filename}")
    return output_filename



def perform_head_swap(
    target_image_path: str,
    face_image_path: str,
    output_filename: str,
    face_url_cached: str | None = None,
):
    """
    نفس الـ signature المطلوبة.
    لو اكتشف أكتر من وجه -> يعمل swap لكل وجه ويتأكد.
    غير كده -> يرجع للسلوك القديم (single).
    """

    # لو multi-face مش مفعّل
    if not _multi_face_enabled():
        return _perform_head_swap_single(target_image_path, face_image_path, output_filename, face_url_cached)

    # ✅ ده اللي هتضيفيه
    if _is_single_attempt_from_env():
        return _perform_head_swap_single(target_image_path, face_image_path, output_filename, face_url_cached)

    base = cv2.imread(target_image_path)
    if base is None:
        _log("   ❌ Could not read target image (cv2). Fallback single.")
        return _perform_head_swap_single(target_image_path, face_image_path, output_filename, face_url_cached)

# ✅ Split-halves priority (صورتين جنب بعض)
    if _split_two_halves_enabled() and _is_wide_split_image(base):
        _log("   🧩 Wide image detected -> trying split-halves pipeline first...")
        out_split = _perform_split_halves_swap(base, face_image_path, output_filename, face_url_cached)
        if out_split:
            return out_split
        _log("   ⚠️ Split-halves failed -> fallback to face detection / multi-face...")

    bboxes = _detect_faces_opencv(base)

    if len(bboxes) < 2 and base.shape[1] >= int(base.shape[0] * 1.6):
        h, w = base.shape[:2]
        mid = w // 2

        left = base[:, :mid].copy()
        right = base[:, mid:].copy()

        b1 = _detect_faces_opencv(left)
        b2 = _detect_faces_opencv(right)

    # shift right boxes
        b2 = [(x1+mid, y1, x2+mid, y2) for (x1, y1, x2, y2) in b2]

        combined = (b1 or []) + (b2 or [])

    # لو حصل تحسن استخدمهم بدل القديم
        if len(combined) > len(bboxes):
            bboxes = sorted(combined, key=lambda b: b[0])

    # لو مفيش وجوه أو وجه واحد -> السلوك القديم
    if len(bboxes) <= 1:
        return _perform_head_swap_single(target_image_path, face_image_path, output_filename, face_url_cached)

    _log(f"   🧠 Multi-face: detected {len(bboxes)} face(s) -> swapping all.")

    composed = base.copy()
    base_name, base_ext = os.path.splitext(output_filename)
    if not base_ext:
        base_ext = ".png"

    changed_faces = 0
    diff_threshold = float(os.getenv("SEGMIND_FACE_DIFF_THRESHOLD", "6.0"))

    for idx, (x1, y1, x2, y2) in enumerate(bboxes, start=1):
        crop_before = composed[y1:y2, x1:x2].copy()

        tmp_in = f"{base_name}_mf{idx}_IN.png"
        tmp_out = f"{base_name}_mf{idx}_OUT.png"
        cv2.imwrite(tmp_in, crop_before)

        out_path = _perform_head_swap_single(tmp_in, face_image_path, tmp_out, face_url_cached)

        if not out_path or not os.path.exists(out_path):
            _log(f"   ❌ Face {idx}: swap failed (no output).")
            continue

        crop_after = cv2.imread(out_path)
        if crop_after is None:
            _log(f"   ❌ Face {idx}: could not read swapped crop.")
            continue

        if crop_after.shape[:2] != crop_before.shape[:2]:
            crop_after = cv2.resize(crop_after, (crop_before.shape[1], crop_before.shape[0]))

        diff = _mean_abs_diff(crop_before, crop_after)

# ✅ خيار: لو diff صغير، برضه إلصق (لأن segmind أحياناً يغير ملامح خفيف)
        force_paste = os.getenv("SEGMIND_FORCE_PASTE", "1").strip().lower() in ("1","true","yes","y")
        if diff < diff_threshold and not force_paste:
            _log(f"   ⚠️ Face {idx}: change too small (diff={diff:.2f}) -> skipped.")
            continue

        composed[y1:y2, x1:x2] = crop_after
        changed_faces += 1
        _log(f"   ✅ Face {idx}: pasted (diff={diff:.2f}, force={force_paste})")

    if changed_faces < len(bboxes):
        _log(f"   ⚠️ Multi-face verification: changed {changed_faces}/{len(bboxes)} faces only.")

    os.makedirs(os.path.dirname(output_filename) or ".", exist_ok=True)
    cv2.imwrite(output_filename, composed)
    _log(f"   ✅ Saved multi-face final: {output_filename}")
    return output_filename