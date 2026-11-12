"""Free text-to-speech: Edge-TTS (primary) with gTTS as a fallback.

Each scene is synthesized separately so the video assembler knows the exact duration of
every scene and can sync visuals + captions to it. Edge-TTS provides sentence timings that
are used for captions; gTTS has none, so timings are estimated from text length.
"""
import asyncio
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

VOICES = {
    "tamil": {"male": "ta-IN-ValluvarNeural", "female": "ta-IN-PallaviNeural"},
    "english": {"male": "en-IN-PrabhatNeural", "female": "en-IN-NeerjaNeural"},
}
GTTS_LANG = {"tamil": "ta", "english": "en"}


@dataclass
class CaptionChunk:
    start: float   # seconds from start of scene audio
    end: float
    text: str


@dataclass
class SceneAudio:
    scene_id: str
    path: Path
    duration: float
    captions: list[CaptionChunk] = field(default_factory=list)


def synthesize_scenes(scenes, language: str, gender: str, out_dir: Path) -> list[SceneAudio]:
    """scenes: iterable with .id and .text. Returns one SceneAudio per scene."""
    out_dir.mkdir(parents=True, exist_ok=True)
    voice = VOICES.get(language, VOICES["english"]).get(gender, VOICES["english"]["male"])
    results: list[SceneAudio] = []
    used_engines: set[str] = set()
    for scene in scenes:
        mp3 = out_dir / f"{scene.id}.mp3"
        captions: list[CaptionChunk] = []
        engine = "edge-tts"
        try:
            captions = _run(_edge_tts(scene.text, voice, mp3))
        except Exception as exc:  # noqa: BLE001
            print(f"[tts] edge-tts failed for scene '{scene.id}' ({exc!r}); trying gTTS")
            try:
                _gtts(scene.text, GTTS_LANG.get(language, "en"), mp3)
                engine = "gtts"
            except Exception as exc2:  # noqa: BLE001
                print(f"[tts] gTTS failed ({exc2!r}); trying offline fallback")
                engine = _offline_tts(scene.text, GTTS_LANG.get(language, "en"), mp3)
        used_engines.add(engine)
        duration = probe_duration(mp3)
        if not captions:
            captions = _estimate_captions(scene.text, duration)
        results.append(SceneAudio(scene.id, mp3, duration, captions))
    synthesize_scenes.last_engines = sorted(used_engines)  # type: ignore[attr-defined]
    return results


def concat_audio(parts: list[SceneAudio], out_path: Path) -> Path:
    """Join per-scene mp3s into one voiceover.mp3 (re-encoded so concatenation is seamless)."""
    list_file = out_path.with_suffix(".txt")
    list_file.write_text("".join(f"file '{p.path.resolve()}'\n" for p in parts))
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c:a", "libmp3lame", "-b:a", "128k", str(out_path)],
        check=True,
    )
    list_file.unlink(missing_ok=True)
    return out_path


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


# ------------------------------------------------------------------ internals
def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Called from inside a running loop (unlikely: pipeline runs in a worker thread).
    raise RuntimeError("synthesize_scenes must run in a worker thread")


async def _edge_tts(text: str, voice: str, out: Path) -> list[CaptionChunk]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate="+8%", pitch="+2Hz")
    chunks: list[CaptionChunk] = []
    with open(out, "wb") as f:
        async for ev in communicate.stream():
            if ev["type"] == "audio":
                f.write(ev["data"])
            elif ev["type"] in ("SentenceBoundary", "WordBoundary"):
                start = ev["offset"] / 10_000_000          # 100-ns ticks -> seconds
                end = start + ev["duration"] / 10_000_000
                chunks.append(CaptionChunk(start, end, ev["text"]))
    if not out.exists() or out.stat().st_size < 1000:
        raise RuntimeError("edge-tts returned no audio")
    return _merge_word_chunks(chunks)


def _merge_word_chunks(chunks: list[CaptionChunk], max_words: int = 5) -> list[CaptionChunk]:
    """Group word/sentence events into short caption lines (max ~5 words) for readable subtitles."""
    merged: list[CaptionChunk] = []
    for c in chunks:
        words = c.text.split()
        if len(words) <= max_words:
            merged.append(c)
            continue
        per = (c.end - c.start) / len(words)
        for i in range(0, len(words), max_words):
            part = words[i:i + max_words]
            merged.append(CaptionChunk(c.start + i * per, c.start + (i + len(part)) * per, " ".join(part)))
    # Combine consecutive single-word events into lines
    lines: list[CaptionChunk] = []
    buf: list[CaptionChunk] = []
    for c in merged:
        buf.append(c)
        if len(" ".join(b.text for b in buf).split()) >= max_words or re.search(r"[.!?।]$", c.text):
            lines.append(CaptionChunk(buf[0].start, buf[-1].end, " ".join(b.text for b in buf)))
            buf = []
    if buf:
        lines.append(CaptionChunk(buf[0].start, buf[-1].end, " ".join(b.text for b in buf)))
    return lines


def _gtts(text: str, lang: str, out: Path) -> None:
    from gtts import gTTS

    gTTS(text=text, lang=lang, slow=False).save(str(out))


def _offline_tts(text: str, lang: str, out: Path) -> str:
    """Last resort so the pipeline never dies: espeak-ng if installed, otherwise silence
    with a duration estimated from the text (the video still renders; voice is missing)."""
    import shutil

    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if exe:
        wav = out.with_suffix(".wav")
        subprocess.run([exe, "-v", lang, "-s", "165", "-w", str(wav), text], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "128k", str(out)], check=True)
        wav.unlink(missing_ok=True)
        return "espeak"
    seconds = max(2.0, len(text.split()) / 2.4)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
         "-t", f"{seconds:.2f}", "-b:a", "64k", str(out)],
        check=True,
    )
    return "silent"


def _estimate_captions(text: str, duration: float, max_words: int = 5) -> list[CaptionChunk]:
    words = text.split()
    if not words:
        return []
    per = duration / len(words)
    out = []
    for i in range(0, len(words), max_words):
        part = words[i:i + max_words]
        out.append(CaptionChunk(i * per, min(duration, (i + len(part)) * per), " ".join(part)))
    return out
