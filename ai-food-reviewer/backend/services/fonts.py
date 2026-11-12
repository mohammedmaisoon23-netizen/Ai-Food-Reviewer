"""Font resolution with Tamil support. Mixed Tamil/Latin text is drawn run-by-run because
Pillow has no automatic font fallback."""
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont

_TAMIL = re.compile(r"[஀-௿]")

_TAMIL_CANDIDATES = [
    "Noto Sans Tamil:bold", "Lohit Tamil", "Noto Sans Tamil", "FreeSans", "Latha", "Nirmala UI",
]
_LATIN_CANDIDATES = ["Noto Sans:bold", "DejaVu Sans:bold", "Liberation Sans:bold", "FreeSans:bold"]
_FALLBACK_FILES = [
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _fc_match(pattern: str) -> str | None:
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}|%{family}", pattern], capture_output=True, text=True, timeout=5).stdout
        path, _, family = out.partition("|")
        want = pattern.split(":")[0].lower()
        if path and Path(path).exists() and want.split()[0] in family.lower():
            return path
    except Exception:
        pass
    return None


@lru_cache(maxsize=None)
def _font_path(kind: str) -> str:
    env = os.getenv("FONT_TAMIL" if kind == "tamil" else "FONT_LATIN")
    if env and Path(env).exists():
        return env
    for pat in (_TAMIL_CANDIDATES if kind == "tamil" else _LATIN_CANDIDATES):
        p = _fc_match(pat)
        if p:
            return p
    for f in _FALLBACK_FILES:
        if Path(f).exists():
            return f
    raise RuntimeError("No usable TrueType font found. Install fonts-lohit-taml and fonts-dejavu-core, or set FONT_TAMIL/FONT_LATIN.")


@lru_cache(maxsize=64)
def get_font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(kind), size)


def _runs(text: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    for ch in text:
        kind = "tamil" if _TAMIL.match(ch) else "latin"
        if ch.isspace() and runs:
            kind = runs[-1][0]
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    return runs


def text_width(text: str, size: int) -> int:
    return int(sum(get_font(k, size).getlength(t) for k, t in _runs(text)))


def draw_text(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, size: int, fill, stroke_width: int = 0,
              stroke_fill=(0, 0, 0), anchor_center: bool = False) -> int:
    x, y = xy
    if anchor_center:
        x -= text_width(text, size) // 2
    for kind, run in _runs(text):
        f = get_font(kind, size)
        draw.text((x, y), run, font=f, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        x += int(f.getlength(run))
    return x


def wrap_text(text: str, size: int, max_width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if text_width(trial, size) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines
