"""AI Food Reviewer Engine - FastAPI server.

Routes (all also available under /api/...):
  POST /generate-review        start a job  -> {"id": ...}
  GET  /status/{id}            progress / result
  GET  /reviews                recent jobs
  GET  /characters             avatar choices
  GET  /health
Static: /media/{id}/review.mp4 (generated files) and the built React app at "/".
"""
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import database as db
from config import CORS_ORIGINS, FRONTEND_DIST, LLM_API_KEY, OUTPUT_DIR
from pipeline import run_pipeline
from services.characters import CHARACTERS, list_characters

app = FastAPI(title="AI Food Reviewer Engine", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["*"], allow_headers=["*"])

# One render at a time: video encoding is CPU/RAM heavy (important on small Render instances).
executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="render")


class GenerateRequest(BaseModel):
    shop: str = Field(..., min_length=2, max_length=300, description="Shop name, 'name, area', or Google Maps link")
    dishes: str = Field("", max_length=300, description="Optional comma-separated dishes to feature")
    language: Literal["tamil", "english"] = "tamil"
    character: str = "arun"


router = APIRouter()


@router.post("/generate-review", status_code=202)
def generate_review(req: GenerateRequest):
    if req.character not in CHARACTERS:
        raise HTTPException(400, f"Unknown character '{req.character}'")
    rid = db.create_review(req.shop.strip(), req.dishes.strip(), req.language, req.character)
    executor.submit(run_pipeline, rid)
    return {"id": rid, "status": "queued", "status_url": f"/api/status/{rid}"}


@router.get("/status/{rid}")
def status(rid: str):
    r = db.get_review(rid)
    if not r:
        raise HTTPException(404, "Review not found")
    base = f"/media/{rid}"
    return {
        "id": r["id"], "status": r["status"], "stage": r["stage"], "progress": r["progress"], "error": r["error"],
        "shop_name": r["shop_name"], "shop_area": r["shop_area"], "language": r["language"], "character": r["character"],
        "script": r["script"],
        "video_url": f"{base}/review.mp4" if r["status"] == "done" else None,
        "audio_url": f"{base}/review.mp3" if r["status"] == "done" else None,
        "subtitles_url": f"{base}/review.srt" if r["status"] == "done" else None,
        "poster_url": f"{base}/review.jpg" if r["status"] == "done" else None,
    }


@router.get("/reviews")
def reviews(limit: int = 12):
    return [
        {"id": r["id"], "shop_name": r["shop_name"] or r["shop_input"], "status": r["status"], "character": r["character"],
         "language": r["language"], "created_at": r["created_at"],
         "poster_url": f"/media/{r['id']}/review.jpg" if r["status"] == "done" else None}
        for r in db.list_reviews(min(max(limit, 1), 50))
    ]


@router.get("/characters")
def characters():
    return list_characters()


@router.get("/health")
def health():
    return {"ok": True, "llm_configured": bool(LLM_API_KEY)}


app.include_router(router, prefix="/api")
app.include_router(router, include_in_schema=False)  # spec-style root paths: /generate-review, /status/{id}


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


app.mount("/media", StaticFiles(directory=OUTPUT_DIR), name="media")

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        candidate = (FRONTEND_DIST / full_path).resolve()
        if full_path and FRONTEND_DIST.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
