# -*- coding: utf-8 -*-
"""
🛠️ Utility Functions
================================================
- Info reader
- Image helpers
- Similarity
- Face crop with robust saving (cv2 + PIL fallback)
"""

import os
import json
import cv2


def read_info_file(folder_path):
    """Read info.txt (JSON-like) from the selected story folder."""
    info_file_path = os.path.join(folder_path, "info.txt")

    en_story_name = None
    ar_story_name = None
    resolution_slides = None
    first_slide_font = None
    rest_slides_font = None
    ar_first_slide_font = None
    ar_rest_slides_font = None

    if os.path.exists(info_file_path):
        try:
            with open(info_file_path, "r", encoding="utf-8") as f:
                content = f.read()

                # Normalize to valid JSON
                content = content.replace('"FIRST_SLIDE_FONT" =', '"FIRST_SLIDE_FONT":')
                content = content.replace('"REST_SLIDES_FONT" =', '"REST_SLIDES_FONT":')
                content = content.replace('"AR_FIRST_SLIDE_FONT" =', '"AR_FIRST_SLIDE_FONT":')
                content = content.replace('"AR_REST_SLIDES_FONT" =', '"AR_REST_SLIDES_FONT":')
                content = content.replace('""', '"')

                data = json.loads(content)

                en_story_name = data.get("en")
                ar_story_name = data.get("ar")
                resolution_slides = data.get("resolution_slides")
                first_slide_font = data.get("FIRST_SLIDE_FONT")
                rest_slides_font = data.get("REST_SLIDES_FONT")
                ar_first_slide_font = data.get("AR_FIRST_SLIDE_FONT")
                ar_rest_slides_font = data.get("AR_REST_SLIDES_FONT")

        except Exception as e:
            print(f"⚠️ Error reading info.txt: {e}")

    return (
        en_story_name,
        ar_story_name,
        resolution_slides,
        first_slide_font,
        rest_slides_font,
        ar_first_slide_font,
        ar_rest_slides_font,
    )


def get_image_dimensions(image_path):
    """Return (width, height) for an image path or None."""
    if not os.path.exists(image_path):
        return None

    img = cv2.imread(image_path)
    if img is None:
        return None

    h, w = img.shape[:2]
    return w, h


def calculate_closest_aspect_ratio(width: int, height: int) -> str:
    """Return the closest supported aspect ratio string."""
    try:
        if not width or not height:
            return "16:9"

        r = width / float(height)

        candidates = {
            "1:1": 1.0,
            "16:9": 16 / 9,
            "9:16": 9 / 16,
            "4:3": 4 / 3,
            "3:4": 3 / 4,
        }

        best = min(candidates.items(), key=lambda kv: abs(r - kv[1]))[0]
        return best
    except Exception:
        return "16:9"


def flip_image_horizontal(image):
    """Mirror flip an image horizontally."""
    if image is None:
        return None
    return cv2.flip(image, 1)


def compare_images_similarity(image1_path, image2_path):
    """
    Compare similarity between two images using SSIM.
    Accepts paths or numpy arrays.
    Returns float in [0,1] or None.
    """
    try:
        from skimage.metrics import structural_similarity as ssim

        if isinstance(image1_path, str):
            img1 = cv2.imread(image1_path)
        else:
            img1 = image1_path

        if isinstance(image2_path, str):
            img2 = cv2.imread(image2_path)
        else:
            img2 = image2_path

        if img1 is None or img2 is None:
            return None

        gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)

        if gray1.shape != gray2.shape:
            gray2 = cv2.resize(gray2, (gray1.shape[1], gray1.shape[0]))

        return float(ssim(gray1, gray2))

    except ImportError:
        print("   ⚠️  scikit-image not installed. Install with: pip install scikit-image")
        return None
    except Exception as e:
        print(f"   ⚠️  Error comparing images: {str(e)}")
        return None


# =========================
# Robust saving helpers
# =========================
def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        print(f"[ERROR] Could not create directory: {path} -> {e}")


def _safe_write_cv2(path: str, img) -> bool:
    try:
        folder = os.path.dirname(path)
        if folder:
            _ensure_dir(folder)

        ok = cv2.imwrite(path, img)
        if ok and os.path.exists(path) and os.path.getsize(path) > 0:
            return True

        print("[ERROR] cv2.imwrite returned False or file not created.")
        print("       path:", path)
        return False

    except Exception as e:
        print("[ERROR] cv2.imwrite exception:", e)
        print("       path:", path)
        return False


def _safe_write_pil(path: str, img_bgr) -> bool:
    try:
        from PIL import Image

        folder = os.path.dirname(path)
        if folder:
            _ensure_dir(folder)

        rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        im = Image.fromarray(rgb)
        im.save(path)

        return os.path.exists(path) and os.path.getsize(path) > 0

    except Exception as e:
        print("[ERROR] PIL save exception:", e)
        print("       path:", path)
        return False


def crop_face_only(image_path, output_path, padding=2):
    """
    Crop to face using Haar Cascade, with rotation attempts.
    Robustly saves the output (cv2 first, PIL fallback).
    Returns saved path or None.
    """
    import numpy as np

    def rotate_image(image, angle):
        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)

        cos = np.abs(rotation_matrix[0, 0])
        sin = np.abs(rotation_matrix[0, 1])
        new_w = int((h * sin) + (w * cos))
        new_h = int((h * cos) + (w * sin))

        rotation_matrix[0, 2] += (new_w / 2) - center[0]
        rotation_matrix[1, 2] += (new_h / 2) - center[1]

        rotated = cv2.warpAffine(
            image,
            rotation_matrix,
            (new_w, new_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
        return rotated

    def detect_and_crop(img, angle_name="original"):
        face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = img.shape[:2]

        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))
        if len(faces) == 0:
            return None

        x, y, width, height = faces[0]

        pad_w = int(width * (padding - 1) / 2)
        pad_h = int(height * (padding - 1) / 2)

        x1 = max(0, x - pad_w)
        y1 = max(0, y - pad_h)
        x2 = min(w, x + width + pad_w)
        y2 = min(h, y + height + pad_h)

        cropped = img[y1:y2, x1:x2]
        print(f"   ✂️  Face cropped ({x1},{y1}) -> ({x2},{y2}) | angle: {angle_name}")
        return cropped

    try:
        img = cv2.imread(image_path)
        if img is None:
            print(f"   ❌ Failed to read image: {image_path}")
            return None

        # Try 1: original
        print("   🔍 Try 1: original...")
        cropped = detect_and_crop(img, "original")
        if cropped is not None:
            if _safe_write_cv2(output_path, cropped) or _safe_write_pil(output_path, cropped):
                return output_path
            return None

        # Try 2: rotate -45
        print("   🔍 Try 2: rotate -45...")
        rotated_cw = rotate_image(img, -45)
        cropped = detect_and_crop(rotated_cw, "-45")
        if cropped is not None:
            if _safe_write_cv2(output_path, cropped) or _safe_write_pil(output_path, cropped):
                return output_path
            return None

        # Try 3: rotate +45
        print("   🔍 Try 3: rotate +45...")
        rotated_ccw = rotate_image(img, 45)
        cropped = detect_and_crop(rotated_ccw, "+45")
        if cropped is not None:
            if _safe_write_cv2(output_path, cropped) or _safe_write_pil(output_path, cropped):
                return output_path
            return None

        # Fallback: save original
        print("   ⚠️  No face detected. Saving original image as fallback.")
        if _safe_write_cv2(output_path, img) or _safe_write_pil(output_path, img):
            return output_path

        return None

    except Exception as e:
        print(f"   ❌ crop_face_only error: {str(e)}")
        return None
