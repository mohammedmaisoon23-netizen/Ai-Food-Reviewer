"""Automated vlog-style video assembly.

Pipeline
  1. Per-scene voiceover (from tts_service) defines the timeline.
  2. Every frame is composed with Pillow: scene background (your own B-roll clips if present in
     assets/broll/, otherwise built-in illustrated scenes), the consistent AI avatar with
     audio-driven lip-sync, eating animation, rating stars, burned-in captions, and an
     "AI-generated" badge. Frames are streamed into FFmpeg (libx264).
  3. FFmpeg mixes the voiceover with background music (auto-ducked under the voice) and muxes
     the final MP4 (+ SRT subtitles and a poster image).

Optional: set TALKING_HEAD_CMD to run an external talking-head engine (Wav2Lip, SadTalker, a
ComfyUI/LTX wrapper, ...) per scene; its clips replace the built-in animated avatar.
"""
from __future__ import annotations

import math
import shlex
import zlib
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from config import (AI_WATERMARK, BROLL_DIR, FFMPEG_PRESET, MUSIC_DIR, PORTRAIT_PATH, TALKING_HEAD_CMD, TALKING_HEAD_TIMEOUT,
                    TALKING_HEAD_TOKEN, TALKING_HEAD_URL,
                    VIDEO_FPS, VIDEO_HEIGHT, VIDEO_WIDTH)
from services.characters import avatar_sprite, get_character, portrait
from services.fonts import draw_text, text_width, wrap_text
from services.tts_service import SceneAudio, concat_audio

ProgressCb = Callable[[int, str], None]
BASE_W, BASE_H = 720, 1280

SCENE_THEMES = {
    "intro":    {"sky": ((255, 154, 90), (120, 70, 150)), "label": "INTRO"},
    "entering": {"sky": ((255, 196, 110), (235, 110, 90)), "label": "ENTERING"},
    "tasting":  {"sky": ((62, 38, 34), (120, 70, 48)), "label": "TASTING"},
    "verdict":  {"sky": ((255, 214, 120), (255, 120, 90)), "label": "VERDICT"},
    "outro":    {"sky": ((80, 70, 200), (230, 80, 150)), "label": "OUTRO"},
}


@dataclass
class Timeline:
    scene_id: str
    start: float
    end: float
    emotion: str


# ============================================================================ public API
def assemble_video(*, shop_name: str, shop_area: str, title: str, rating: float, scenes, scene_audio: list[SceneAudio],
                   character_id: str, work_dir: Path, out_path: Path, progress: Optional[ProgressCb] = None) -> dict:
    """Builds the final MP4. Returns {"video", "audio", "srt", "poster", "duration", "talking_head"}."""
    progress = progress or (lambda p, s: None)
    work_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    W = VIDEO_WIDTH - VIDEO_WIDTH % 2
    H = VIDEO_HEIGHT - VIDEO_HEIGHT % 2
    fps = VIDEO_FPS

    voice_path = concat_audio(scene_audio, work_dir / "voiceover.mp3")
    timeline, t0 = [], 0.0
    emotions = {s.id: getattr(s, "emotion", "happy") for s in scenes}
    for sa in scene_audio:
        timeline.append(Timeline(sa.scene_id, t0, t0 + sa.duration, emotions.get(sa.scene_id, "happy")))
        t0 += sa.duration
    total = t0
    n_frames = int(math.ceil(total * fps))

    # ---- optional external talking-head clips --------------------------------------------------
    progress(66, "Talking-head engine" if (TALKING_HEAD_CMD or TALKING_HEAD_URL) else "Preparing avatar")
    head_clips = _run_talking_head(character_id, scene_audio, work_dir) if (TALKING_HEAD_CMD or TALKING_HEAD_URL) else None
    use_builtin_avatar = head_clips is None

    # ---- captions + srt --------------------------------------------------------------------------
    captions = _build_captions(scene_audio, timeline)
    srt_path = out_path.with_suffix(".srt")
    _write_srt(captions, srt_path)

    # ---- render frames -> silent video -------------------------------------------------------------
    silent = work_dir / "silent.mp4"
    envelope = _audio_envelope(voice_path, fps, n_frames)
    renderer = FrameRenderer(W, H, fps, shop_name, shop_area, title, rating, character_id, timeline, captions,
                             envelope, draw_avatar=use_builtin_avatar)
    poster_frame = int(n_frames * 0.55)
    poster_path = out_path.with_suffix(".jpg")

    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", FFMPEG_PRESET, "-crf", "23", "-pix_fmt", "yuv420p", str(silent)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        last_pct = -1
        for f in range(n_frames):
            frame = renderer.render(f)
            if f == poster_frame:
                frame.save(poster_path, quality=88)
            enc.stdin.write(frame.tobytes())
            pct = 68 + int(24 * f / max(1, n_frames))
            if pct != last_pct:
                progress(pct, f"Rendering video {int(100 * f / n_frames)}%")
                last_pct = pct
        enc.stdin.close()
        rc = enc.wait()
        if rc != 0:
            raise RuntimeError("FFmpeg encode failed: " + enc.stderr.read().decode(errors="ignore")[-600:])
    finally:
        renderer.close()
        if enc.poll() is None:
            enc.kill()

    # ---- optional overlay of external avatar clips ---------------------------------------------
    video_in = silent
    if head_clips:
        progress(93, "Overlaying talking-head clips")
        video_in = _overlay_head_clips(silent, head_clips, work_dir, W, H)

    # ---- audio mix + mux -----------------------------------------------------------------------
    progress(95, "Mixing audio & music")
    music = _pick_music(work_dir, total)
    _mux(video_in, voice_path, music, total, out_path)

    audio_out = out_path.with_suffix(".mp3")
    audio_out.write_bytes(voice_path.read_bytes())
    progress(100, "Done")
    return {"video": out_path, "audio": audio_out, "srt": srt_path, "poster": poster_path,
            "duration": round(total, 1), "talking_head": bool(head_clips)}


# ============================================================================ captions / srt
def _build_captions(scene_audio: list[SceneAudio], timeline: list[Timeline]) -> list[tuple[float, float, str]]:
    caps: list[tuple[float, float, str]] = []
    for sa, tl in zip(scene_audio, timeline):
        for c in sa.captions:
            caps.append((tl.start + c.start, min(tl.end, tl.start + c.end), c.text.strip()))
    caps = [c for c in caps if c[2]]
    caps.sort(key=lambda c: c[0])
    # Hold each caption until the next starts (avoids flicker in tiny gaps).
    out = []
    for i, (s, e, t) in enumerate(caps):
        nxt = caps[i + 1][0] if i + 1 < len(caps) else e
        if 0 <= nxt - e < 0.45:
            e = nxt
        out.append((s, max(e, s + 0.3), t))
    return out


def _fmt_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(caps, path: Path) -> None:
    path.write_text("\n".join(f"{i}\n{_fmt_srt(s)} --> {_fmt_srt(e)}\n{t}\n" for i, (s, e, t) in enumerate(caps, 1)), encoding="utf-8")


# ============================================================================ audio helpers
def _audio_envelope(audio: Path, fps: int, n_frames: int) -> np.ndarray:
    """Per-video-frame mouth openness 0..1 derived from the voiceover loudness."""
    sr = 16000
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(audio), "-f", "s16le", "-ac", "1", "-ar", str(sr), "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = sr / fps
    env = np.zeros(n_frames, dtype=np.float32)
    if x.size and float(np.abs(x).max()) > 1e-4:
        for i in range(n_frames):
            seg = x[int(i * hop): int((i + 1) * hop)]
            env[i] = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
        ref = float(np.percentile(env[env > 0], 92)) if np.any(env > 0) else 1.0
        env = np.clip(env / max(ref, 1e-6), 0, 1)
    else:  # silent fallback voice: fake a natural talking rhythm so the avatar still animates
        t = np.arange(n_frames) / fps
        env = np.clip(0.55 + 0.35 * np.sin(t * 11.0) * np.sin(t * 3.1 + 1.0), 0, 1)
    k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
    return np.convolve(env, k, mode="same")


def _pick_music(work_dir: Path, total: float) -> Path:
    tracks = sorted(p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"))
    if tracks:
        return tracks[int(total * 10) % len(tracks)]
    return _synth_music(work_dir / "bgm.wav")


def _synth_music(path: Path, bpm: int = 104, sr: int = 32000) -> Path:
    """Generates a royalty-free upbeat loop (pad chords + soft kick/hat). No downloads, no licensing."""
    beat = 60.0 / bpm
    bar = beat * 4
    chords = [(220.00, 261.63, 329.63), (174.61, 220.00, 261.63), (261.63, 329.63, 392.00), (196.00, 246.94, 293.66)]
    n_total = int(bar * len(chords) * sr)
    t_all = np.arange(n_total) / sr
    sig = np.zeros(n_total, dtype=np.float32)
    for ci, freqs in enumerate(chords):
        a, b = int(ci * bar * sr), int((ci + 1) * bar * sr)
        t = t_all[a:b] - t_all[a]
        env = np.minimum(1, t / 0.15) * np.minimum(1, (bar - t) / 0.25)
        for f in freqs:
            sig[a:b] += (np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t)).astype(np.float32) * env.astype(np.float32) * 0.12
        # plucky arpeggio
        for step in range(8):
            ts = int((ci * bar + step * beat / 2) * sr)
            f = freqs[step % 3] * 2
            m = min(int(0.25 * sr), n_total - ts)
            if m > 0:
                tt = np.arange(m) / sr
                sig[ts:ts + m] += (np.sin(2 * np.pi * f * tt) * np.exp(-tt * 14) * 0.10).astype(np.float32)
    rng = np.random.default_rng(7)
    for beat_i in range(len(chords) * 4):
        ts = int(beat_i * beat * sr)
        m = min(int(0.18 * sr), n_total - ts)
        tt = np.arange(m) / sr
        sig[ts:ts + m] += (np.sin(2 * np.pi * (110 - 60 * tt / 0.18) * tt) * np.exp(-tt * 22) * 0.35).astype(np.float32)  # kick
        h = int((beat_i * beat + beat / 2) * sr)
        hm = min(int(0.05 * sr), n_total - h)
        if hm > 0:
            sig[h:h + hm] += (rng.standard_normal(hm) * np.exp(-np.arange(hm) / sr * 70) * 0.05).astype(np.float32)  # hat
    sig = np.clip(sig / max(1e-6, np.abs(sig).max()) * 0.8, -1, 1)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", int(v * 32767)) for v in sig[::1]))
    return path


def _mux(video: Path, voice: Path, music: Path, total: float, out: Path) -> None:
    graph = (
        "[0:a]asplit=2[v1][v2];"
        "[1:a]volume=0.55,aformat=sample_rates=44100:channel_layouts=stereo[m];"
        "[v1]aformat=sample_rates=44100:channel_layouts=stereo[vs];"
        "[v2]aformat=sample_rates=44100:channel_layouts=stereo[vo];"
        "[m][vs]sidechaincompress=threshold=0.015:ratio=12:attack=20:release=450[duck];"
        "[vo][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
    )
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice), "-stream_loop", "-1", "-i", str(music),
           "-i", str(video), "-filter_complex", graph, "-map", "2:v", "-map", "[a]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("FFmpeg mux failed: " + r.stderr[-600:])


# ============================================================================ talking-head hook
def _run_talking_head(character_id: str, scene_audio: list[SceneAudio], work_dir: Path) -> Optional[list[Path]]:
    """Per-scene realistic talking-head clips from a remote GPU worker (TALKING_HEAD_URL) or a local
    command (TALKING_HEAD_CMD). Returns clip paths, or None to fall back to the built-in avatar."""
    img = work_dir / "portrait.png"
    if PORTRAIT_PATH.exists():
        Image.open(PORTRAIT_PATH).convert("RGB").resize((512, 512), Image.LANCZOS).save(img)
    else:
        portrait(character_id, 512).save(img)
    clips: list[Path] = []
    for n, sa in enumerate(scene_audio):
        out = work_dir / f"head_{sa.scene_id}.mp4"
        try:
            if TALKING_HEAD_URL:
                _remote_head(img, sa.path, out)
            else:
                args = [a.format(image=str(img), audio=str(sa.path), output=str(out)) for a in shlex.split(TALKING_HEAD_CMD)]
                subprocess.run(args, check=True, timeout=TALKING_HEAD_TIMEOUT, capture_output=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[talking-head] failed on scene {sa.scene_id}: {exc!r}; using built-in avatar")
            return None
        if not out.exists() or out.stat().st_size < 1000:
            return None
        clips.append(out)
    return clips


def _remote_head(image: Path, audio: Path, out: Path) -> None:
    import httpx

    headers = {"X-Token": TALKING_HEAD_TOKEN} if TALKING_HEAD_TOKEN else {}
    with open(image, "rb") as fi, open(audio, "rb") as fa:
        r = httpx.post(f"{TALKING_HEAD_URL}/animate", headers=headers, timeout=TALKING_HEAD_TIMEOUT,
                       files={"image": ("portrait.png", fi, "image/png"), "audio": ("scene.mp3", fa, "audio/mpeg")})
    r.raise_for_status()
    out.write_bytes(r.content)


def _overlay_head_clips(base: Path, clips: list[Path], work_dir: Path, W: int, H: int) -> Path:
    inputs, chains = [], []
    for i, c in enumerate(clips):
        inputs += ["-i", str(c)]
        chains.append(f"[{i + 1}:v]scale={int(W * 0.66)}:-2,setsar=1,fps={VIDEO_FPS},format=yuv420p[h{i}]")
    concat = "".join(f"[h{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[head]"
    graph = ";".join(chains + [concat, f"[0:v][head]overlay=x=(W-w)/2:y={int(H * 0.27)}:eof_action=pass[v]"])
    out = work_dir / "with_heads.mp4"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(base), *inputs, "-filter_complex", graph, "-map", "[v]",
                        "-c:v", "libx264", "-preset", FFMPEG_PRESET, "-crf", "23", "-pix_fmt", "yuv420p", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("[talking-head] overlay failed:", r.stderr[-300:])
        return base
    return out


# ============================================================================ frame renderer
class FrameRenderer:
    def __init__(self, W, H, fps, shop_name, shop_area, title, rating, character_id, timeline, captions, envelope, draw_avatar=True):
        self.W, self.H, self.fps, self.k = W, H, fps, W / BASE_W
        self.shop_name, self.shop_area, self.title, self.rating = shop_name, shop_area, title, rating
        self.cid, self.timeline, self.captions, self.env = character_id, timeline, captions, envelope
        self.draw_avatar = draw_avatar
        self.char = get_character(character_id)
        self._bg_cache: dict[str, Image.Image] = {}
        self._cap_cache: dict[str, Image.Image] = {}
        self._sprite_cache: dict[tuple, Image.Image] = {}
        self._broll = self._list_broll()
        self._broll_iter: Optional[Iterator[Image.Image]] = None
        self._broll_scene: Optional[int] = None
        self._broll_proc: Optional[subprocess.Popen] = None
        self._prev_frame: Optional[Image.Image] = None
        self._header = self._make_header()

    # ---- helpers
    def S(self, v: float) -> int:
        return int(round(v * self.k))

    def close(self) -> None:
        self._stop_broll()

    # ---- B-roll
    @staticmethod
    def _list_broll() -> list[Path]:
        return sorted(p for p in BROLL_DIR.glob("*") if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm"))

    def _stop_broll(self) -> None:
        if self._broll_proc and self._broll_proc.poll() is None:
            self._broll_proc.kill()
        self._broll_proc = None
        self._broll_iter = None

    def _broll_frames(self, clip: Path, duration: float) -> Iterator[Image.Image]:
        W, H = self.W, self.H
        cmd = ["ffmpeg", "-loglevel", "error", "-stream_loop", "-1", "-i", str(clip), "-t", f"{duration + 1:.2f}",
               "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={self.fps},eq=brightness=-0.06",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        self._broll_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        size = W * H * 3
        while True:
            buf = self._broll_proc.stdout.read(size)
            if len(buf) < size:
                return
            yield Image.frombytes("RGB", (W, H), buf)

    def _background(self, idx: int, tl: Timeline) -> Image.Image:
        if self._broll:
            if self._broll_scene != idx:
                self._stop_broll()
                self._broll_scene = idx
                self._broll_iter = self._broll_frames(self._broll[idx % len(self._broll)], tl.end - tl.start)
            try:
                if self._broll_iter is not None:
                    return next(self._broll_iter).copy()
            except StopIteration:
                pass
        return self._procedural_bg(tl.scene_id).copy()

    # ---- procedural scenes
    def _procedural_bg(self, scene_id: str) -> Image.Image:
        if scene_id in self._bg_cache:
            return self._bg_cache[scene_id]
        W, H = BASE_W, BASE_H
        theme = SCENE_THEMES.get(scene_id, SCENE_THEMES["intro"])
        top, bot = theme["sky"]
        arr = np.zeros((H, W, 3), dtype=np.uint8)
        for c in range(3):
            arr[:, :, c] = np.linspace(top[c], bot[c], H, dtype=np.float32)[:, None].astype(np.uint8)
        img = Image.fromarray(arr, "RGB")
        d = ImageDraw.Draw(img, "RGBA")
        rng = np.random.default_rng(zlib.crc32(scene_id.encode()))

        if scene_id in ("intro", "entering"):
            # skyline + street
            x = -20
            while x < W:
                w = int(rng.integers(70, 130)); h = int(rng.integers(220, 520))
                col = (40 + int(rng.integers(0, 25)), 30, 60 + int(rng.integers(0, 30)), 255)
                d.rectangle([x, 880 - h, x + w, 900], fill=col)
                for wy in range(880 - h + 20, 880, 38):
                    for wx in range(x + 12, x + w - 14, 26):
                        if rng.random() > 0.45:
                            d.rectangle([wx, wy, wx + 12, wy + 18], fill=(255, 220, 130, 220))
                x += w + int(rng.integers(4, 18))
            d.rectangle([0, 900, W, H], fill=(58, 54, 66, 255))
            d.rectangle([0, 900, W, 925], fill=(150, 145, 150, 255))
            for lx in range(30, W, 140):
                d.rectangle([lx, 1100, lx + 70, 1112], fill=(240, 220, 120, 230))
        if scene_id == "entering":
            # shop front
            d.rectangle([60, 330, W - 60, 900], fill=(250, 238, 215, 255))
            for i, sx in enumerate(range(60, W - 60, 60)):
                d.polygon([(sx, 330), (sx + 60, 330), (sx + 50, 410), (sx + 10, 410)],
                          fill=(225, 70, 60, 255) if i % 2 == 0 else (255, 245, 235, 255))
            d.rounded_rectangle([90, 215, W - 90, 320], radius=22, fill=(35, 28, 40, 255), outline=(255, 200, 60, 255), width=6)
            d.rounded_rectangle([W // 2 - 95, 520, W // 2 + 95, 900], radius=14, fill=(115, 72, 48, 255))
            d.ellipse([W // 2 + 60, 700, W // 2 + 78, 718], fill=(255, 215, 90, 255))
            d.rectangle([100, 470, 270, 700], fill=(255, 238, 170, 255), outline=(90, 60, 40, 255), width=6)
            d.rectangle([W - 270, 470, W - 100, 700], fill=(255, 238, 170, 255), outline=(90, 60, 40, 255), width=6)
            lines = wrap_text(self.shop_name.upper(), 42, W - 220)[:2]
            ty = 268 - 24 * (len(lines) - 1) if lines else 250
            for ln in lines:
                draw_text(d, (W // 2, ty - 22), ln, 42 if len(lines) == 1 else 34, (255, 214, 90), anchor_center=True)
                ty += 44
        elif scene_id == "tasting":
            # warm interior
            for lx in (120, 360, 600):
                d.line([(lx, 0), (lx, 150)], fill=(30, 20, 15, 255), width=4)
                d.pieslice([lx - 50, 130, lx + 50, 230], 180, 360, fill=(255, 200, 90, 255))
                d.ellipse([lx - 130, 140, lx + 130, 400], fill=(255, 200, 100, 28))
            d.rectangle([0, 700, W, 760], fill=(86, 52, 36, 255))
            d.rectangle([0, 760, W, H], fill=(112, 70, 46, 255))
            for py in range(780, H, 60):
                d.line([(0, py), (W, py)], fill=(90, 54, 36, 255), width=3)
        elif scene_id == "verdict":
            d.ellipse([-200, 700, W + 200, 1500], fill=(255, 255, 255, 40))
            d.ellipse([W // 2 - 360, 140, W // 2 + 360, 860], fill=(255, 255, 255, 30))
        elif scene_id == "outro":
            for i in range(26):
                cx, cy, r = int(rng.integers(0, W)), int(rng.integers(0, H)), int(rng.integers(10, 60))
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, int(rng.integers(15, 45))))
            d.rounded_rectangle([110, 215, W - 110, 330], radius=60, fill=(255, 40, 60, 255))
            draw_text(d, (W // 2, 245), "SUBSCRIBE", 54, (255, 255, 255), anchor_center=True)
        img = img.resize((self.W, self.H), Image.LANCZOS) if (self.W, self.H) != (BASE_W, BASE_H) else img
        self._bg_cache[scene_id] = img
        return img

    # ---- overlays
    def _make_header(self) -> Image.Image:
        layer = Image.new("RGBA", (self.W, self.S(150)), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        name = self.shop_name if text_width(self.shop_name, self.S(34)) < self.W - self.S(140) else self.shop_name[:22] + "…"
        pw = min(self.W - self.S(40), text_width(name, self.S(34)) + self.S(70))
        d.rounded_rectangle([self.S(20), self.S(26), self.S(20) + pw, self.S(86)], radius=self.S(30), fill=(0, 0, 0, 150))
        draw_text(d, (self.S(46), self.S(34)), name, self.S(34), (255, 255, 255))
        if AI_WATERMARK:
            lbl = "AI-GENERATED"
            lw = text_width(lbl, self.S(20)) + self.S(26)
            d.rounded_rectangle([self.W - lw - self.S(20), self.S(96), self.W - self.S(20), self.S(130)], radius=self.S(17), fill=(255, 255, 255, 210))
            draw_text(d, (self.W - lw - self.S(7), self.S(101)), lbl, self.S(20), (40, 40, 50))
        return layer

    def _caption_layer(self, text: str) -> Image.Image:
        if text in self._cap_cache:
            return self._cap_cache[text]
        size = self.S(46)
        lines = wrap_text(text, size, self.W - self.S(90))[:3]
        lh = int(size * 1.35)
        h = lh * len(lines) + self.S(30)
        layer = Image.new("RGBA", (self.W, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle([self.S(24), 0, self.W - self.S(24), h], radius=self.S(26), fill=(0, 0, 0, 120))
        y = self.S(14)
        for ln in lines:
            draw_text(d, (self.W // 2, y), ln, size, (255, 255, 255), stroke_width=max(2, self.S(3)), stroke_fill=(0, 0, 0), anchor_center=True)
            y += lh
        if len(self._cap_cache) > 300:
            self._cap_cache.clear()
        self._cap_cache[text] = layer
        return layer

    def _sprite(self, mouth: int, blink: bool, brows: bool, chew: bool, scale: float) -> Image.Image:
        key = (mouth, blink, brows, chew, round(scale, 2))
        if key not in self._sprite_cache:
            sp = avatar_sprite(self.cid, mouth, blink, brows, chew)
            w = int(sp.width * scale * self.k)
            self._sprite_cache[key] = sp.resize((w, int(sp.height * scale * self.k)), Image.LANCZOS)
        return self._sprite_cache[key]

    @staticmethod
    def _star(cx, cy, r, n=5):
        pts = []
        for i in range(n * 2):
            a = -math.pi / 2 + i * math.pi / n
            rad = r if i % 2 == 0 else r * 0.42
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        return pts

    # ---- per-frame
    def render(self, f: int) -> Image.Image:
        t = f / self.fps
        idx = next((i for i, tl in enumerate(self.timeline) if t < tl.end), len(self.timeline) - 1)
        tl = self.timeline[idx]
        lt = t - tl.start
        frame = self._background(idx, tl).convert("RGBA")
        layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        k = self.k

        level_f = float(self.env[min(f, len(self.env) - 1)])
        mouth = 0 if level_f < 0.14 else 1 if level_f < 0.4 else 2 if level_f < 0.7 else 3
        blink = (f % (self.fps * 3 + 5)) < 3
        brows = tl.emotion in ("excited", "delighted") and level_f > 0.55

        # avatar placement
        sc = 1.0
        ax = self.W / 2
        bob = math.sin(t * 5.0) * 5 * k * (0.4 + level_f)
        chew = False
        if tl.scene_id == "entering":
            walk = min(1.0, lt / 2.2)
            ease = 1 - (1 - walk) ** 3
            ax = -self.S(260) + (self.W / 2 + self.S(260)) * ease
            bob = abs(math.sin(lt * 9)) * -14 * k * (1 - walk) + bob * walk
            sc = 0.82 + 0.18 * ease
        if tl.scene_id == "tasting":
            cycle = (lt % 2.6)
            chew = 1.0 <= cycle < 1.9 and level_f < 0.3
        if tl.scene_id == "verdict":
            sc = 1.0 + 0.03 * math.sin(t * 3)

        if self.draw_avatar:
            sp = self._sprite(0 if chew else mouth, blink, brows, chew, sc)
            ay = self.S(1000) - sp.height + int(bob)
            if tl.scene_id == "tasting":
                ay = self.S(1010) - sp.height + int(bob)
            layer.alpha_composite(sp, (int(ax - sp.width / 2), max(0, ay)))
            if tl.scene_id == "tasting":
                self._draw_plate(d)
                self._draw_eating(d, lt, ax, ay, sp)

        # scene specific overlays
        if tl.scene_id == "verdict":
            self._draw_rating(layer, d, lt)
        if tl.scene_id == "outro":
            tag = "Like · Share · Subscribe"
            draw_text(d, (self.W // 2, self.S(352)), tag, self.S(34), (255, 255, 255), stroke_width=2, anchor_center=True)
        if tl.scene_id == "intro" and lt < 3.2:
            ttl = wrap_text(self.title, self.S(44), self.W - self.S(100))[:2]
            a = int(255 * min(1, lt / 0.4) * min(1, (3.2 - lt) / 0.4))
            yy = self.S(210)
            for ln in ttl:
                draw_text(d, (self.W // 2, yy), ln, self.S(44), (255, 255, 255, a), stroke_width=3, stroke_fill=(0, 0, 0, a), anchor_center=True)
                yy += self.S(58)

        frame.alpha_composite(layer)
        frame.alpha_composite(self._header, (0, 0))

        cap = next((c for c in self.captions if c[0] <= t < c[1]), None)
        if cap:
            cl = self._caption_layer(cap[2])
            frame.alpha_composite(cl, (0, self.S(1010)))

        out = frame.convert("RGB")
        # crossfade transition at scene change
        if idx > 0 and lt < 0.35 and self._prev_frame is not None:
            out = Image.blend(self._prev_frame, out, min(1.0, lt / 0.35))
        self._prev_frame = out
        return out

    def _draw_plate(self, d: ImageDraw.ImageDraw) -> None:
        S = self.S
        d.ellipse([S(170), S(925), S(550), S(1010)], fill=(0, 0, 0, 70))
        d.ellipse([S(180), S(900), S(540), S(990)], fill=(250, 248, 245, 255), outline=(200, 195, 190, 255), width=max(2, S(4)))
        d.ellipse([S(212), S(912), S(508), S(970)], fill=(238, 232, 226, 255))
        for (fx, fy, fr, fc) in [(300, 940, 34, (205, 120, 45)), (360, 952, 38, (236, 236, 226)), (425, 940, 30, (190, 90, 40)),
                                 (335, 930, 20, (110, 160, 60)), (400, 962, 22, (240, 200, 90))]:
            d.ellipse([S(fx - fr), S(fy - fr * 0.7), S(fx + fr), S(fy + fr * 0.7)], fill=fc + (255,))

    def _draw_eating(self, d: ImageDraw.ImageDraw, lt: float, ax: float, ay: int, sp: Image.Image) -> None:
        cycle = lt % 2.6
        plate = (self.W * 0.5, self.S(945))
        mouth = (ax + sp.width * 0.05, ay + sp.height * 0.53)
        if cycle < 1.0:
            p = cycle / 1.0
        else:
            return
        e = 0.5 - 0.5 * math.cos(math.pi * p)            # plate -> mouth
        x = plate[0] + (mouth[0] - plate[0]) * e
        y = plate[1] + (mouth[1] - plate[1]) * e
        r = self.S(30)
        d.ellipse([x - r, y - r, x + r, y + r], fill=self.char.skin + (255,))                   # hand
        fr = self.S(20)
        d.ellipse([x - fr, y - fr * 1.6, x + fr, y - fr * 0.2], fill=(214, 128, 46, 255))      # food bite

    def _draw_rating(self, layer: Image.Image, d: ImageDraw.ImageDraw, lt: float) -> None:
        full = self.rating
        cy = self.S(250)
        gap = self.S(110)
        x0 = self.W / 2 - 2 * gap
        for i in range(5):
            appear = min(1.0, max(0.0, (lt - 0.3 - i * 0.18) / 0.25))
            if appear <= 0:
                continue
            r = self.S(48) * appear
            d.polygon(self._star(x0 + i * gap, cy, self.S(48)), fill=(0, 0, 0, 90))
            frac = max(0.0, min(1.0, full - i))
            d.polygon(self._star(x0 + i * gap, cy, r), fill=(255, 255, 255, 120))
            if frac > 0:
                # partially filled star via mask
                m = Image.new("L", (self.W, self.H), 0)
                md = ImageDraw.Draw(m)
                md.polygon(self._star(x0 + i * gap, cy, r), fill=255)
                md.rectangle([x0 + i * gap - r + 2 * r * frac, 0, self.W, self.H], fill=0)
                layer.paste((255, 196, 40, 255), (0, 0), m)
        if lt > 1.4:
            draw_text(d, (self.W // 2, self.S(305)), f"{full:.1f} / 5", self.S(64), (255, 255, 255), stroke_width=4, anchor_center=True)
