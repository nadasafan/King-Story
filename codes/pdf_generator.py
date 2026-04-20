# -*- coding: utf-8 -*-
"""
📄 PDF Generator Module
تحويل الصور إلى PDF باستخدام PIL

قاعدة المشروع: لا يُنشأ ملف قصة (PDF) بدون نص قصة صالح.
Override طارئ فقط: ALLOW_PDF_WITHOUT_STORY_TEXT=1
"""

import cv2
from PIL import Image
from datetime import datetime
import uuid
import os
from pathlib import Path

from story_ai import MIN_STORY_TEXT_PLAIN_LEN

try:
    from config import PDF_PIL_DPI
except ImportError:
    PDF_PIL_DPI = 72.0


def create_pdf_from_images(
    images_list,
    output_path,
    use_parallel=None,
    story_text: str | None = None,
    min_story_text_len: int | None = None,
    trace_id: str | None = None,
):
    """
    إنشاء PDF من قائمة الصور باستخدام PIL

    Args:
        images_list: قائمة الصور (OpenCV format - BGR)
        output_path: مسار ملف PDF الناتج
        use_parallel: غير مستخدم (للتوافق مع الكود القديم)
        story_text: النص المسطح للقصة (إلزامي ما لم يُفعّل التجاوز الطارئ)
        min_story_text_len: الحد الأدنى لطول النص؛ الافتراضي من story_ai

    Returns:
        output path str on success, False on failure
    """
    _p = lambda msg: print(f"### [PDF_TRACE:{trace_id}] {msg}" if trace_id else msg, flush=True)

    allow_no_text = os.environ.get("ALLOW_PDF_WITHOUT_STORY_TEXT", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if min_story_text_len is None:
        min_story_text_len = MIN_STORY_TEXT_PLAIN_LEN

    if not allow_no_text:
        st = (story_text or "").strip()
        if not st:
            _p(
                "STEP_PDF_01_FAIL empty story_text | ERROR: لا يُنشأ PDF للقصة بدون نص. / Cannot create story PDF: story_text is empty."
            )
            return False
        if len(st) < min_story_text_len:
            _p(
                f"STEP_PDF_01_FAIL short story_text len={len(st)} min={min_story_text_len}"
            )
            return False

    if not images_list:
        _p("STEP_PDF_01_FAIL no images")
        return False

    _p(f"STEP_PDF_02_PIL_START pages={len(images_list)}")
    print("\nCreating PDF...")
    # تحويل OpenCV images إلى PIL Images
    pil_images = []
    
    for idx, img in enumerate(images_list, 1):
        # تحويل BGR (OpenCV) → RGB (PIL)
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(img_rgb)
        
        # تحويل RGBA → RGB إذا لزم الأمر
        if pil_img.mode == 'RGBA':
            # إنشاء خلفية بيضاء
            rgb_img = Image.new('RGB', pil_img.size, (255, 255, 255))
            # لصق الصورة مع استخدام قناة الشفافية كـ mask
            rgb_img.paste(pil_img, mask=pil_img.split()[3])
            pil_images.append(rgb_img)
        else:
            # تحويل إلى RGB
            pil_images.append(pil_img.convert('RGB'))
        
        print(f"   Converting image {idx}/{len(images_list)}")
        if trace_id and idx <= 3:
            _p(f"STEP_PDF_03_PAGE sample[{idx}] shape={getattr(img, 'shape', None)}")

    if not pil_images:
        _p("STEP_PDF_FAIL no pil_images")
        print("ERROR: No valid images to save")
        return False

    # حفظ كـ PDF
    _p("STEP_PDF_04_SAVE_PIL")
    print("Writing PDF...")
    try:
        # توليد اسم PDF جديد
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        unique_key = uuid.uuid4().hex[:6]

        folder = os.path.dirname(output_path)
        base = os.path.splitext(os.path.basename(output_path))[0]

        new_pdf_name = f"{base}_{timestamp}_{unique_key}.pdf"
        output_path = os.path.join(folder, new_pdf_name)

        dpi = float(PDF_PIL_DPI)
        if dpi <= 0:
            dpi = 72.0
        print(f"   PDF embed DPI={dpi} (set PDF_PIL_DPI; 72 ≈ 1pt per pixel)", flush=True)

        pil_images[0].save(
            output_path,
            "PDF",
            resolution=dpi,
            save_all=True,
            append_images=pil_images[1:] if len(pil_images) > 1 else None,
        )

        _p(f"STEP_PDF_05_DONE path={output_path}")
        print(f"Done: {output_path}")
        return output_path

    except Exception as e:
        _p(f"STEP_PDF_FAIL exception {e!r}")
        print(f"ERROR: Failed to create PDF - {e}")
        return False


