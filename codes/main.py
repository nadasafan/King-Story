# -*- coding: utf-8 -*-
"""
🎭 Stories Production (Reversed)
Main pipeline runner:
- UI selection
- Head swap
- Text rendering
- Resize
- PDF export
"""

import sys
import os
from PySide6.QtWidgets import QApplication

from config import RESULT_FOLDER, USE_PARALLEL_TEXT_PROCESSING, BASE_DIR
from utils import read_info_file
from ui_selector import (
    select_language,
    select_gender,
    select_story,
    show_character_images,
    get_user_name,
)
from text_handler import load_custom_fonts, read_text_data
from image_processor import process_head_swap, apply_text_to_images, apply_resolution_to_images
from pdf_generator import create_pdf_from_images


def _print_header():
    print("\n" + "=" * 40)
    print("Stories Production")
    print("=" * 40)


def _resolve_text_file(translations_folder: str, language: str, en_story_name: str, ar_story_name: str):
    if language == "en":
        text_file = os.path.join(translations_folder, "en_text_data.txt")
        pdf_name = en_story_name if en_story_name else "Story_EN"
        return text_file, pdf_name

    ar_files = [f for f in os.listdir(translations_folder) if f.startswith("ar_")]
    if not ar_files:
        return None, None

    text_file = os.path.join(translations_folder, ar_files[0])
    pdf_name = ar_story_name if ar_story_name else "Story_AR"
    return text_file, pdf_name


from datetime import datetime
import uuid

def _build_pdf_filename(pdf_name: str, language: str, user_name: str):

    if language == "en":
        pdf_filename = (
            pdf_name.replace("Name", user_name)
            .replace("name", user_name)
            .replace("NAME", user_name.upper())
        )
    else:
        pdf_filename = (
            pdf_name.replace("الاسم", user_name)
            .replace("اسم", user_name)
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    unique_key = uuid.uuid4().hex[:6]

    return f"{pdf_filename}_{timestamp}_{unique_key}.pdf"


def main():
    _print_header()

    language = select_language()
    gender, gender_folder = select_gender()

    story_folder = select_story(gender)
    if not story_folder:
        sys.exit(1)

    character_image_path, character_name = show_character_images(gender_folder)
    if not character_image_path:
        sys.exit(1)

    user_name = get_user_name(language)
    if not user_name:
        sys.exit(1)

    (
        en_story_name,
        ar_story_name,
        resolution_slides,
        first_slide_font,
        rest_slides_font,
        ar_first_slide_font,
        ar_rest_slides_font,
    ) = read_info_file(story_folder)

    translations_folder = os.path.join(story_folder, "Translations")
    text_file, pdf_name = _resolve_text_file(translations_folder, language, en_story_name, ar_story_name)
    if not text_file or not pdf_name or not os.path.exists(text_file):
        sys.exit(1)

    text_data = read_text_data(text_file, user_name=user_name, language=language)
    if not text_data:
        sys.exit(1)

    # Setup Qt only for sequential text rendering
    app = None
    fonts_loaded = None

    if (not USE_PARALLEL_TEXT_PROCESSING) or (len(text_data) <= 1):
        app = QApplication(sys.argv)

        selected_first_font = first_slide_font if language == "en" else ar_first_slide_font
        selected_rest_font = rest_slides_font if language == "en" else ar_rest_slides_font

        fonts_loaded = load_custom_fonts(
            language=language,
            first_slide_font_path=selected_first_font,
            rest_slides_font_path=selected_rest_font,
            base_dir=BASE_DIR,
        )

    # Head swap / generation phase
    processed_images_dict, original_dims_dict = process_head_swap(
        clean_images_folder=None,
        character_image_path=character_image_path,
        character_name=character_name,
        story_folder=story_folder,
    )

    if not processed_images_dict:
        sys.exit(1)

    # Text overlay phase
    selected_first_font = first_slide_font if language == "en" else ar_first_slide_font
    selected_rest_font = rest_slides_font if language == "en" else ar_rest_slides_font

    images_with_text = apply_text_to_images(
        images_dict=processed_images_dict,
        text_data=text_data,
        original_dims_dict=original_dims_dict,
        app=app,
        fonts_loaded=fonts_loaded,
        language=language,
        first_slide_font=selected_first_font,
        rest_slides_font=selected_rest_font,
    )

    if not images_with_text:
        sys.exit(1)

    # Resize phase (if needed)
    if not resolution_slides:
        final_images = [images_with_text[name] for name in sorted(images_with_text.keys())]
    else:
        final_images = apply_resolution_to_images(
            images_dict=images_with_text,
            resolution_slides=resolution_slides,
        )

    if not final_images:
        sys.exit(1)

    # PDF export
    os.makedirs(RESULT_FOLDER, exist_ok=True)
    pdf_filename = _build_pdf_filename(pdf_name, language, user_name)
    pdf_path = os.path.join(RESULT_FOLDER, pdf_filename)

    success = create_pdf_from_images(final_images, pdf_path)

    if app:
        app.quit()

    if not success:
        sys.exit(1)

    print("\n" + "=" * 40)
    print(f"Done: {pdf_path}")
    print("=" * 40 + "\n")


if __name__ == "__main__":
    main()
 

 