"""Free-GPU talking-head worker (runs on Google Colab). The Render app POSTs a face image + scene audio
to /animate and gets back an mp4 of that face speaking the audio.

HEAD_CMD is the engine command; placeholders {image} {audio} {outdir}. It must leave an .mp4 in {outdir}.
Default = SadTalker. Swap in Wav2Lip / MuseTalk / LivePortrait / Hallo by changing HEAD_CMD.
"""
import glob
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

TOKEN = os.getenv("WORKER_TOKEN", "")
HEAD_CMD = os.getenv(
    "HEAD_CMD",
    "python inference.py --driven_audio {audio} --source_image {image} --result_dir {outdir} "
    "--still --preprocess full --size 256 --enhancer gfpgan",
)
ENGINE_DIR = os.getenv("ENGINE_DIR", "/content/SadTalker")

app = FastAPI()


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/animate")
def animate(image: UploadFile = File(...), audio: UploadFile = File(...), x_token: str = Header("")):
    if TOKEN and x_token != TOKEN:
        raise HTTPException(401, "bad token")
    tmp = Path(tempfile.mkdtemp())
    try:
        img, aud, outdir = tmp / "face.png", tmp / "voice.mp3", tmp / "out"
        outdir.mkdir()
        img.write_bytes(image.file.read())
        aud.write_bytes(audio.file.read())
        cmd = HEAD_CMD.format(image=img, audio=aud, outdir=outdir)
        r = subprocess.run(cmd, shell=True, cwd=ENGINE_DIR, capture_output=True, text=True)
        vids = sorted(glob.glob(str(outdir / "**" / "*.mp4"), recursive=True), key=os.path.getmtime)
        if r.returncode != 0 or not vids:
            raise HTTPException(500, "engine failed: " + (r.stderr or r.stdout)[-500:])
        keep = Path(tempfile.mkstemp(suffix=".mp4")[1])
        shutil.copy(vids[-1], keep)
        return FileResponse(keep, media_type="video/mp4")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
