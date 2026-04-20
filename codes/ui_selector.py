# -*- coding: utf-8 -*-
"""
🎮 UI Selector Module
Interactive CLI selectors for language, gender, story, character, and user name.
"""

import os
from config import STORIES_FOLDER, CHARACTERS_FOLDER


def select_language():
    """Select UI language (ar/en)."""
    print("\n" + "=" * 60)
    print("🌍 Choose Language:")
    print("=" * 60)
    print("1) Arabic")
    print("2) English")

    while True:
        choice = input("\nEnter 1 or 2: ").strip()
        if choice == "1":
            return "ar"
        if choice == "2":
            return "en"
        print("Invalid choice. Please enter 1 or 2.")


def select_gender():
    """Select gender (boy/girl) and return (gender_key, gender_folder_name)."""
    print("\n" + "=" * 60)
    print("👤 Choose Gender:")
    print("=" * 60)
    print("1) Boy")
    print("2) Girl")

    while True:
        choice = input("\nEnter 1 or 2: ").strip()
        if choice == "1":
            return "boy", "Boys"
        if choice == "2":
            return "girl", "Girls"
        print("Invalid choice. Please enter 1 or 2.")


def get_available_stories(gender):
    """Return available stories under Stories/Boys or Stories/Girls."""
    if not os.path.isdir(STORIES_FOLDER):
        return []

    gender_folder_name = "Boys" if gender == "boy" else "Girls"
    gender_folder_path = os.path.join(STORIES_FOLDER, gender_folder_name)

    if not os.path.isdir(gender_folder_path):
        return []

    stories = []
    for item in os.listdir(gender_folder_path):
        story_path = os.path.join(gender_folder_path, item)
        if os.path.isdir(story_path):
            stories.append(item)

    return sorted(stories)


def select_story(gender):
    """Select a story folder path based on gender."""
    stories = get_available_stories(gender)
    if not stories:
        print("No stories found for this selection.")
        return None

    gender_folder_name = "Boys" if gender == "boy" else "Girls"

    print("\n" + "=" * 60)
    print(f"📚 Available Stories ({gender_folder_name}):")
    print("=" * 60)

    for idx, story in enumerate(stories, 1):
        print(f"{idx}) {story}")

    while True:
        raw = input(f"\nChoose a story (1-{len(stories)}): ").strip()
        try:
            choice = int(raw)
        except ValueError:
            print("Invalid input. Please enter a number.")
            continue

        if 1 <= choice <= len(stories):
            selected_story = stories[choice - 1]
            story_path = os.path.join(STORIES_FOLDER, gender_folder_name, selected_story)
            print(f"Selected: {selected_story}")
            return story_path

        print(f"Please choose a number between 1 and {len(stories)}.")


def show_character_images(gender_folder):
    """
    Select a character image from Characters/{gender_folder}.
    Returns (cropped_face_path_or_original, character_name).
    """
    from config import TEMP_CROPPED_FOLDER
    from utils import crop_face_only

    char_path = os.path.join(CHARACTERS_FOLDER, gender_folder)
    if not os.path.isdir(char_path):
        print(f"Characters folder not found: {char_path}")
        return None, None

    images = [
        f for f in os.listdir(char_path)
        if f.lower().endswith((".png", ".jpg", ".jpeg"))
    ]

    if not images:
        print(f"No character images found in: {char_path}")
        return None, None

    images = sorted(images)

    print(f"\n📸 Available Characters ({gender_folder}):")
    for idx, img in enumerate(images, 1):
        print(f"{idx}) {img}")

    while True:
        raw = input(f"\nChoose an image (1-{len(images)}): ").strip()
        try:
            choice = int(raw)
        except ValueError:
            print("Invalid input. Please enter a number.")
            continue

        if not (1 <= choice <= len(images)):
            print(f"Please choose a number between 1 and {len(images)}.")
            continue

        selected_image = images[choice - 1]
        selected_image_path = os.path.join(char_path, selected_image)
        character_name = os.path.splitext(selected_image)[0]
        print(f"Selected: {selected_image}")

        # Auto-crop face (best effort)
        print("✂️  Cropping face...")
        os.makedirs(TEMP_CROPPED_FOLDER, exist_ok=True)
        cropped_image_path = os.path.join(TEMP_CROPPED_FOLDER, f"cropped_{selected_image}")
        print("\n" + "="*70)
        print("[DEBUG] TEMP_CROPPED_FOLDER:", TEMP_CROPPED_FOLDER)
        print("[DEBUG] cropped_image_path:", cropped_image_path)
        print("="*70 + "\n")


        try:
            result_path = crop_face_only(selected_image_path, cropped_image_path, padding=2)
            print("\n" + "="*70)    
            print("[DEBUG] crop_face_only returned:", result_path)  
            print("[DEBUG] exists on disk:", os.path.exists(result_path) if result_path else False)
            print("="*70 + "\n")

        except Exception:
            result_path = None

        if result_path:
            print(f"Face crop saved: {cropped_image_path}")
            return result_path, character_name

        print("Face crop failed. Using original image.")
        return selected_image_path, character_name


def get_user_name(language):
    """Ask for user name."""
    print("\n" + "=" * 60)
    prompt = "Enter the hero/heroine name: "
    user_name = input(prompt).strip()

    if not user_name:
        print("No name provided.")
        return None

    print(f"Name received: {user_name}")
    return user_name
