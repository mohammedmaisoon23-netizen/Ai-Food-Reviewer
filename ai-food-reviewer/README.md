# AI Food Reviewer Engine

Shop name / location / Google Maps link -> vlog-style script -> voiceover -> captioned 9:16 video starring a consistent AI character.

```
frontend (React + Tailwind) --> FastAPI --> script (Groq LLM) --> TTS (Edge-TTS) --> video (Pillow + FFmpeg)
                                   |
                                SQLite + local files (data/)
```

| Piece | File |
|---|---|
| API + job routes | `backend/main.py`, `backend/pipeline.py` |
| Script prompts | `backend/services/script_generator.py` |
| Free TTS (Edge-TTS, gTTS, offline fallback) | `backend/services/tts_service.py` |
| Video assembly (lip-sync, captions, music, mux) | `backend/services/video_assembler.py` |
| Avatars | `backend/services/characters.py` |
| UI | `frontend/src/App.jsx` |

## Run on GitHub Actions (no PC, no server)

GitHub's servers render the video; you only fill a form.

1. Create a GitHub repo and push this folder. A **public** repo has free unlimited Actions minutes; private repos get 2,000 free minutes a month.
2. Repo -> **Settings -> Secrets and variables -> Actions -> New repository secret** -> name `GROQ_API_KEY`, value = your free Groq key
   (optional; without it a template script is used).
3. Repo -> **Actions** tab -> **Generate food review video** -> **Run workflow** (also works from the GitHub mobile app).
   Enter the shop name or Google Maps link, dishes, language and character. Leave the shop empty to render every line of `shops.txt` as a batch.
4. When the run turns green (about 5-15 minutes), open it and download the **food-review-videos** artifact: `review.mp4`, `review.mp3`,
   `review.srt`, `review.jpg`, `script.json`. Artifacts are kept for 30 days.

Actions has no GPU, so the realistic-human Colab option below is not used there; Actions videos use the animated avatar.

## Run locally

Prereqs: Python 3.10+, Node 18+, FFmpeg (`ffmpeg -version` must work).

Install FFmpeg: Windows `winget install Gyan.FFmpeg` | macOS `brew install ffmpeg` | Ubuntu `sudo apt install ffmpeg fonts-lohit-taml`.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # paste GROQ_API_KEY (free at console.groq.com)
uvicorn main:app --reload --port 8000

# Frontend (new terminal)
cd frontend
npm install
npm run dev                                            # http://localhost:5173
```

Without `GROQ_API_KEY` a built-in template script is used, so you can test everything immediately.

## Deploy on Render (free)

1. Push this folder to a GitHub repo (the repo root must contain `Dockerfile` and `render.yaml`).
2. Render dashboard -> **New +** -> **Blueprint** -> pick the repo -> **Apply**.
   (Or **New + -> Web Service** -> Runtime **Docker**, Health Check Path `/api/health`.)
3. In the service's **Environment** tab add `GROQ_API_KEY`. Deploy.
4. Open the `https://<name>.onrender.com` URL: the React UI and API are served from the same service.

Free-tier notes: the service sleeps after ~15 min idle (first load ~1 min); CPU is small, so a 60 s video takes several minutes;
disk is ephemeral, so generated files vanish on redeploy/restart (download what you want to keep).

## Realistic human (free GPU via Google Colab)

Render's free CPU cannot run realistic talking-head models. Run them on a free Colab GPU and let the Render app call it:

1. Put a face photo at `backend/assets/characters/portrait.png` (a front-facing, well-lit face). Use an **AI-generated fictional
   face** or **yourself**. Never use a real creator's or other person's face without their consent.
2. Open `colab/head_worker_colab.ipynb` in Google Colab, set a **T4 GPU** runtime, run all cells, and copy the printed
   `TALKING_HEAD_URL` and `TALKING_HEAD_TOKEN`. Upload `colab/head_worker.py` to Colab (or point the wget line at your repo).
3. Add both values to Render -> Environment and redeploy. Each scene is sent to Colab and the returned clip is overlaid on the scene.
   If the worker is offline or fails, the app falls back to the cartoon avatar automatically.

Expect slow renders (minutes per scene) and Colab session limits. The app-to-worker handoff is tested; the model install cell
(SadTalker) depends on Colab's current Python/library versions and may need tweaks - swap `HEAD_CMD` for Wav2Lip, MuseTalk or LivePortrait if it fights you.

## Responsible use

Videos carry an "AI-GENERATED" badge (`AI_WATERMARK=true`). The script prompt forbids invented prices, awards or hygiene
claims, and the character never claims to have really eaten the food. Keep that when publishing, and only use B-roll you
have rights to. Don't present reviews as genuine customer experiences.
