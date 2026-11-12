# ---------- Stage 1: build the React app ----------
FROM node:20-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- Stage 2: FastAPI + FFmpeg ----------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data

# ffmpeg for video, fontconfig + Tamil/Latin fonts for captions
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg fontconfig fonts-lohit-taml fonts-dejavu-core espeak-ng \
    && rm -rf /var/lib/apt/lists/* && fc-cache -f

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
COPY --from=frontend /app/frontend/dist frontend/dist

WORKDIR /app/backend
RUN mkdir -p /data
EXPOSE 10000
# Render injects $PORT; default 10000 for local `docker run`
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-10000}"]
