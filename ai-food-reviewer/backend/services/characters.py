"""Consistent AI avatar characters.

Each character is a fixed set of design parameters drawn procedurally with Pillow, so the same
face/outfit is used in every frame of every video (zero model downloads, zero cost). The same
portrait PNG can be fed to an external talking-head engine (see video_assembler / TALKING_HEAD_CMD).
"""
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw

SS = 2  # supersampling factor for smooth edges


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    tagline: str
    gender: str          # "male" | "female" (picks the TTS voice)
    skin: tuple
    hair: tuple
    shirt: tuple
    accent: tuple
    style: str           # hair/face style: "short" | "long" | "cap" | "beard"


CHARACTERS: dict[str, Character] = {
    "arun": Character("arun", "Arun", "Street-food explorer", "male", (205, 150, 105), (30, 24, 22), (225, 70, 55), (255, 200, 60), "beard"),
    "meena": Character("meena", "Meena", "Foodie queen", "female", (222, 168, 122), (28, 20, 24), (40, 150, 140), (255, 120, 150), "long"),
    "karthi": Character("karthi", "Karthi", "Biryani hunter", "male", (190, 135, 95), (24, 20, 20), (60, 100, 200), (255, 170, 40), "cap"),
}


def list_characters() -> list[dict]:
    return [{"id": c.id, "name": c.name, "tagline": c.tagline, "gender": c.gender,
             "color": "#%02x%02x%02x" % c.shirt} for c in CHARACTERS.values()]


def get_character(cid: str) -> Character:
    return CHARACTERS.get(cid, CHARACTERS["arun"])


# mouth level 0 closed, 1 small, 2 open, 3 wide
@lru_cache(maxsize=256)
def avatar_sprite(cid: str, mouth: int, blink: bool, brows_up: bool, chew: bool = False) -> Image.Image:
    ch = get_character(cid)
    W, H = 460, 620
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = SS
    cx = W // 2

    def box(x0, y0, x1, y1):
        return [x0 * s, y0 * s, x1 * s, y1 * s]

    shirt_dark = tuple(max(0, c - 40) for c in ch.shirt)
    skin_dark = tuple(max(0, c - 35) for c in ch.skin)

    # body / shirt
    d.rounded_rectangle(box(cx - 190, 400, cx + 190, 700), radius=110 * s, fill=ch.shirt)
    d.polygon([(cx * s - 55 * s, 400 * s), (cx * s + 55 * s, 400 * s), (cx * s, 470 * s)], fill=shirt_dark)
    # accent stripe (shirt logo)
    d.rounded_rectangle(box(cx - 60, 520, cx + 60, 545), radius=12 * s, fill=ch.accent)
    # neck
    d.rounded_rectangle(box(cx - 42, 350, cx + 42, 440), radius=30 * s, fill=skin_dark)

    # hair back (long style)
    if ch.style == "long":
        d.rounded_rectangle(box(cx - 152, 110, cx - 78, 470), radius=45 * s, fill=ch.hair)
        d.rounded_rectangle(box(cx + 78, 110, cx + 152, 470), radius=45 * s, fill=ch.hair)

    # ears
    d.ellipse(box(cx - 150, 245, cx - 112, 310), fill=ch.skin)
    d.ellipse(box(cx + 112, 245, cx + 150, 310), fill=ch.skin)
    # head
    d.ellipse(box(cx - 128, 100, cx + 128, 395), fill=ch.skin)

    # hair front
    if ch.style == "cap":
        d.pieslice(box(cx - 135, 80, cx + 135, 330), 180, 360, fill=ch.shirt)
        d.rounded_rectangle(box(cx - 20, 182, cx + 170, 206), radius=10 * s, fill=ch.shirt)
        d.rectangle(box(cx - 135, 200, cx + 135, 212), fill=ch.accent)
    elif ch.style == "long":
        d.pieslice(box(cx - 135, 80, cx + 135, 330), 180, 360, fill=ch.hair)
        d.polygon([(cx * s - 135 * s, 200 * s), (cx * s - 40 * s, 140 * s), (cx * s + 135 * s, 200 * s),
                   (cx * s + 135 * s, 150 * s), (cx * s, 90 * s), (cx * s - 135 * s, 150 * s)], fill=ch.hair)
    else:
        d.pieslice(box(cx - 135, 80, cx + 135, 330), 180, 360, fill=ch.hair)

    # beard
    if ch.style == "beard":
        d.pieslice(box(cx - 128, 220, cx + 128, 430), 0, 180, fill=ch.hair)
        d.ellipse(box(cx - 62, 292, cx + 62, 352), fill=ch.skin)

    # eyes
    ey = 245
    for ex in (cx - 52, cx + 52):
        if blink:
            d.arc(box(ex - 24, ey - 6, ex + 24, ey + 18), 200, 340, fill=(40, 30, 30), width=5 * s)
        else:
            d.ellipse(box(ex - 22, ey - 24, ex + 22, ey + 22), fill=(255, 255, 255))
            d.ellipse(box(ex - 11, ey - 13, ex + 11, ey + 11), fill=(45, 30, 25))
            d.ellipse(box(ex - 5, ey - 10, ex + 1, ey - 4), fill=(255, 255, 255))
    # eyebrows
    by = ey - 48 - (12 if brows_up else 0)
    for ex, sign in ((cx - 52, -1), (cx + 52, 1)):
        tilt = 8 if brows_up else 0
        d.line([(ex * s - 28 * s, (by + sign * tilt * -1) * s), (ex * s + 28 * s, (by + sign * tilt) * s)],
               fill=ch.hair if ch.style != "cap" else (40, 30, 25), width=9 * s)
    # nose + cheeks
    d.ellipse(box(cx - 12, 262, cx + 12, 290), fill=skin_dark)
    blush = Image.new("RGBA", img.size, (0, 0, 0, 0))
    bd = ImageDraw.Draw(blush)
    bd.ellipse(box(cx - 100, 290, cx - 62, 318), fill=(255, 110, 110, 85))
    bd.ellipse(box(cx + 62, 290, cx + 100, 318), fill=(255, 110, 110, 85))
    img.alpha_composite(blush)
    d = ImageDraw.Draw(img)

    # mouth
    my = 330 if ch.style != "beard" else 322
    inner = (110, 30, 40)
    if chew:
        d.ellipse(box(cx - 44, my - 14, cx + 44, my + 22), fill=inner)
        d.ellipse(box(cx - 34, my - 4, cx + 34, my + 22), fill=(230, 90, 100))
        d.ellipse(box(cx + 22, my - 22, cx + 70, my + 12), fill=ch.skin)  # full cheek
    elif mouth == 0:
        d.arc(box(cx - 40, my - 30, cx + 40, my + 20), 20, 160, fill=(120, 45, 45), width=6 * s)
    else:
        w = (30, 40, 52)[mouth - 1]
        h = (12, 30, 50)[mouth - 1]
        d.ellipse(box(cx - w, my - h // 3, cx + w, my + h), fill=inner)
        if mouth >= 2:
            d.ellipse(box(cx - w + 12, my + h - 18, cx + w - 12, my + h + 2), fill=(225, 85, 100))
            d.rounded_rectangle(box(cx - w + 8, my - h // 3, cx + w - 8, my - h // 3 + 10), radius=4 * s, fill=(255, 255, 255))

    return img.resize((W, H), Image.LANCZOS)


def portrait(cid: str, size: int = 512) -> Image.Image:
    """Square portrait (head + shoulders) on a flat background, e.g. input for Wav2Lip/SadTalker."""
    ch = get_character(cid)
    sprite = avatar_sprite(cid, 1, False, False)
    bg = Image.new("RGB", (size, size), tuple(int(c * 0.35 + 150) for c in ch.accent))
    scale = size / 520
    sp = sprite.resize((int(sprite.width * scale), int(sprite.height * scale)), Image.LANCZOS)
    bg.paste(sp, ((size - sp.width) // 2, int(10 * scale)), sp)
    return bg
