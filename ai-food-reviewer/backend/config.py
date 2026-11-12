"""Central configuration. Everything is overridable with environment variables."""
import os
from pathlib import Path

try:  # optional: load backend/.env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
OUTPUT_DIR = DATA_DIR / "outputs"
WORK_DIR = DATA_DIR / "work"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.getenv("DB_PATH", DATA_DIR / "reviews.db"))

ASSETS_DIR = BASE_DIR / "assets"
BROLL_DIR = ASSETS_DIR / "broll"      # drop your own .mp4 clips here (optional)
MUSIC_DIR = ASSETS_DIR / "music"      # drop royalty-free .mp3 files here (optional)
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist"))

for _d in (DATA_DIR, OUTPUT_DIR, WORK_DIR, UPLOAD_DIR, BROLL_DIR, MUSIC_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ---- LLM (Groq is free-tier friendly; any OpenAI-compatible endpoint works) ----
LLM_API_KEY = os.getenv("GROQ_API_KEY") or os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "llama-3.3-70b-versatile")

# ---- Video ----
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "720"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "1280"))
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "24"))
FFMPEG_PRESET = os.getenv("FFMPEG_PRESET", "veryfast")
AI_WATERMARK = os.getenv("AI_WATERMARK", "true").lower() in ("1", "true", "yes")
MAX_SCRIPT_SECONDS = int(os.getenv("MAX_SCRIPT_SECONDS", "75"))

# ---- Optional external talking-head engine (Wav2Lip / SadTalker / ComfyUI wrapper) ----
# Command template run per scene. Placeholders: {image} {audio} {output}
# Example: python /opt/Wav2Lip/inference.py --face {image} --audio {audio} --outfile {output}
TALKING_HEAD_CMD = os.getenv("TALKING_HEAD_CMD", "")
TALKING_HEAD_TIMEOUT = int(os.getenv("TALKING_HEAD_TIMEOUT", "900"))
# Remote free-GPU worker (Google Colab notebook in colab/). Base URL, e.g. https://xxxx.trycloudflare.com
TALKING_HEAD_URL = os.getenv("TALKING_HEAD_URL", "").rstrip("/")
TALKING_HEAD_TOKEN = os.getenv("TALKING_HEAD_TOKEN", "")
# Your own realistic face photo (a licensed/AI-generated fictional person, or yourself). Used instead of the cartoon.
PORTRAIT_PATH = Path(os.getenv("PORTRAIT_PATH", ASSETS_DIR / "characters" / "portrait.png"))

# ---- Misc ----
HTTP_USER_AGENT = "AIFoodReviewerEngine/1.0"
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
