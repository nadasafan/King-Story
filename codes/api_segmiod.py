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

from config import (
    SEGMIND_API_KEY,
    SEGMIND_TIMEOUT,
    SEGMIND_SEED,
)

SEGMIND_FACESWAP_V5_URL = "https://api.segmind.com/v1/faceswap-v5"

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
    return requests.post(SEGMIND_FACESWAP_V5_URL, headers=headers, json=data, timeout=timeout)


# ---------------------------
# Main
# ---------------------------
def perform_head_swap(
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
        target_url = _upload_to_segmind_storage(target_image_path)
        if not target_url:
            _log("   ❌ Failed to upload target")
            return None

        # face url: either cached passed in, or upload every time
        if face_url_cached:
            face_url = face_url_cached
        else:
            _log("   ☁️  Uploading face to Segmind Storage...")
            face_url = _upload_to_segmind_storage(face_image_path)
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