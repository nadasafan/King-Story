# -*- coding: utf-8 -*-
"""
🔧 Configuration Module
- Secrets must come from environment variables (.env or system env)
"""

import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ================== WaveSpeed API Configuration ==================
WAVESPEED_API_KEY = "5245087bfe91fb4213628401ccfac875329ee1e2a58518a0daff0c22abb20c3b"
NANO_BANANA_API_URL = "https://api.wavespeed.ai/api/v3/google/nano-banana-pro/edit-ultra"
NANO_BANANA_RESOLUTION = "4k"  # "4k" or "8k" for higher quality
WAVESPEED_OUTPUT_FORMAT = "jpeg"  # أو "png"
WAVESPEED_SYNC_MODE = True  # True = النتيجة فوراً, False = الانتظار عبر polling
WAVESPEED_TIMEOUT = 60 * 7 # 5 minutes 
NANO_BANANA_PROMPT = "Replace ONLY the person's head/face in this image with the head/face from the reference image. IMPORTANT: Keep the EXACT original image dimensions, aspect ratio, and composition - DO NOT crop, resize, or change the image size in any way. Preserve the original pose, body, clothing, lighting, background, and all other elements exactly as they are. Only modify the head/face area to match the reference, ensuring natural blending with realistic skin tones, shadows, and facial expression that matches the original angle and lighting."


GEMINI_API_KEY = "AIzaSyAv8W4cQZod4PAXgI9yAyxGqRZKh1gItH0"
GEMINI_MODEL = "gemini-3-pro-image-preview"
GEMINI_TIMEOUT= 180
GEMINI_IMAGE_SIZE = "2K"   # "1K" | "2K" | "4K"  (K لازم Capital) :contentReference[oaicite:1]{index=1}




# =================Sigmoid=====================
SEGMIND_API_KEY = "SG_a86634f6eea7e457"
SEGMIND_TIMEOUT = 580
SEGMIND_SAFETY  = 3     
import time
SEGMIND_SEED = int(time.time()) 

# ================== ImgBB Configuration ==================
IMGBB_API_KEY = "3b4cd701f4471dee2c2c67a0d13d711e"
IMGBB_UPLOAD_URL = "https://api.imgbb.com/1/upload"

# ================== Font Paths ==================

EN_FIRST_SLIDE_FONT = os.path.abspath(os.path.join(BASE_DIR, "Fonts/english fonts/KidzhoodDEMO-Bold.otf"))
EN_REST_SLIDES_FONT = os.path.abspath(os.path.join(BASE_DIR, "Fonts/english fonts/KidzhoodDEMO-Medium.otf"))

AR_FIRST_SLIDE_FONT = os.path.abspath(os.path.join(BASE_DIR, "Fonts/arabic fonts/KidzhoodArabicDEMO-Light.otf"))
AR_REST_SLIDES_FONT = os.path.abspath(os.path.join(BASE_DIR, "Fonts/arabic fonts/KidzhoodArabicDEMO-Light.otf"))
#==================Openai Conf=====================

OPENAI_API_KEY = "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"
OPENAI_API_KEY = "sk-"

OPENAI_MODEL = "gpt-4o"
# ================== Folder Paths ==================
STORIES_FOLDER = os.path.join(BASE_DIR, "Stories")
CHARACTERS_FOLDER = os.path.join(BASE_DIR, "characters")
RESULT_FOLDER = os.path.join(BASE_DIR, "Result")
TEMP_CROPPED_FOLDER = os.path.join(BASE_DIR, "temp_cropped_faces")

# ================== Text Rendering ==================
ENABLE_TEXT_SHADOW = True
TEXT_SHADOW_STYLE = "2px 2px 4px rgba(0, 0, 0, 0.9)"

# Shadow Effect Settings
SHADOW_BLUR_RADIUS = 4          # درجة الضبابية
SHADOW_COLOR = (0, 0, 0, 255)   # لون الظل (R, G, B, Alpha)
SHADOW_OFFSET_X = 0             # إزاحة الظل أفقياً
SHADOW_OFFSET_Y = 5             # إزاحة الظل عمودياً



# ================== Processing Settings ==================
HEAD_SWAP_DELAY = 0.2  # Delay between API calls in seconds (Reduced from 1s)
RETRY_DELAY = 0.5      # Delay before retrying failed API calls
MAX_RETRIES = 2        # Maximum number of retries for API calls
SIMILARITY_THRESHOLD = 0.97  # Threshold for considering face unchanged (0.0 to 1.0)

# ================== Parallel Processing Settings ==================
API_WORKERS = 3       # Number of simultaneous API calls
UPLOAD_WORKERS = 5    # Number of simultaneous image uploads
USE_PARALLEL_TEXT_PROCESSING = False  # Enable parallel text rendering
from multiprocessing import cpu_count
MAX_TEXT_WORKERS = max(1, cpu_count() - 1)  # Number of parallel workers for text processing



