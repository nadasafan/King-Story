# Docker – Production setup for Stories Studio API

Production-ready image: **Python 3.10**, **PySide6 (Qt offscreen)**, **OpenCV**, **FastAPI**.  
Paths and layout match the project (Fonts, Stories, Result, characters, codes, info.txt). No code changes.

---

## 1. Build

From the **project root** (where `Dockerfile` and `docker-compose.yml` are):

```bash
# Build image
docker build -t stories-studio-api:latest .

# Or with Compose
docker compose build
```

---

## 2. Run

```bash
# Run container (port 8000, restart on failure)
docker run -d --name stories-api -p 8000:8000 --restart unless-stopped stories-studio-api:latest

# Or with Compose
docker compose up -d
```

API: **http://localhost:8000**  
Docs: **http://localhost:8000/docs**

---

## 3. PySide6 / Qt offscreen inside Docker

- **Env:** `QT_QPA_PLATFORM=offscreen` and `QT_OPENGL=software` are set in the Dockerfile and in `docker-compose.yml`.
- **System libs:** The image installs the Qt/PySide6 offscreen stack (e.g. libegl1, libgbm1, libgl1-mesa-glx, libxkbcommon-x11-0, fontconfig, libx11-xcb1, etc.) so Qt runs without a display.
- **Fonts:** Project fonts are under `/app/Fonts` (and `/app/fonts` → symlink to `/app/Fonts`). Your app uses `BASE_DIR` (e.g. `/app`) and existing paths like `Fonts/english fonts/...` and `Fonts/arabic fonts/...`, so no path or code change is required; fonts are loaded as on the host.
- **No text/layout changes:** Same rendering pipeline, no extra scaling or font replacement; only the runtime is containerized.

---

## 4. Optional: bind mounts

To use host folders for outputs or assets, uncomment and adjust in `docker-compose.yml`:

```yaml
volumes:
  - ./Result:/app/result
  - ./Stories:/app/Stories
  - ./Fonts:/app/Fonts
```

---

## 5. Commands summary

| Action        | Command |
|---------------|---------|
| Build         | `docker build -t stories-studio-api:latest .` |
| Run           | `docker run -d -p 8000:8000 --restart unless-stopped stories-studio-api:latest` |
| Compose up    | `docker compose up -d` |
| Compose build | `docker compose build` |
| Logs          | `docker compose logs -f` or `docker logs -f stories-api` |
| Stop          | `docker compose down` or `docker stop stories-api` |

---

## 6. Server inside the container

- **App:** FastAPI app in `codes.api_server.main:app`.
- **Server:** Uvicorn, 4 workers, no reload.
- **Bind:** `0.0.0.0:8000`.
- **Working directory:** `/app/codes` so `BASE_DIR` stays `/app` and paths (Fonts, Stories, result, etc.) stay correct.
