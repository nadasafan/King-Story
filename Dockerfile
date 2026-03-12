# Production Docker for FastAPI + PySide6 (Qt Offscreen) + OpenCV
# Python 3.10 base
FROM python:3.10-slim-bookworm

# Prevent Qt from trying to use display
ENV QT_QPA_PLATFORM=offscreen
ENV QT_OPENGL=software
ENV QT_LOGGING_RULES="*.debug=false;qt.qpa.*=false"

# System deps for PySide6 and Qt Offscreen
RUN apt-get update && apt-get install -y --no-install-recommends \
    libegl1 \
    libgbm1 \
    libgl1-mesa-glx \
    libxkbcommon-x11-0 \
    libgl1-mesa-dri \
    libxcb-xinerama0 \
    libxcb-cursor0 \
    libnss3 \
    libxcomposite1 \
    libxrandr2 \
    libxtst6 \
    libxi6 \
    libxrender1 \
    libxext6 \
    fontconfig \
    libfreetype6 \
    libx11-dev \
    libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project AS IS (folder structure unchanged)
# Build context = project root containing Fonts, Stories, codes, etc.
COPY . /app

# Ensure required dirs exist (API writes to result, TempUploads, temp_cropped_faces)
RUN mkdir -p /app/Result /app/result /app/characters /app/temp_cropped_faces /app/TempUploads

# Symlink so /app/fonts exists; Qt/config still use /app/Fonts via BASE_DIR
RUN ln -sfn /app/Fonts /app/fonts

# Writable for API outputs
RUN chmod -R 755 /app/result /app/Result /app/temp_cropped_faces /app/TempUploads

# Run FastAPI from codes dir (api_server lives under codes)
WORKDIR /app/codes

EXPOSE 8000

# Production: Uvicorn, 4 workers, no reload, bind 0.0.0.0:8000
CMD ["uvicorn", "api_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
