"""Vlogger-style food review script generation.

Uses any OpenAI-compatible chat endpoint (Groq free tier by default). If no API key is
configured or the call fails, a built-in template script is used so the pipeline still runs.

The script is split into scenes so the video assembler can match visuals to narration.
"""
import json
import re
from dataclasses import dataclass, asdict
from typing import Optional

import httpx

from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, MAX_SCRIPT_SECONDS
from services.place_parser import ShopInfo

SCENE_IDS = ["intro", "entering", "tasting", "verdict", "outro"]

LANGUAGE_RULES = {
    "tamil": (
        "Write ALL narration in natural spoken Chennai/Tamil Nadu Tamil using Tamil script (தமிழ்). "
        "Casual street-vlogger tone, a few common English words mixed in (super, taste, review, shop) are fine, "
        "but written in Tamil script too."
    ),
    "english": (
        "Write ALL narration in energetic Indian-English vlogger style. Short punchy sentences. "
        "Light local flavour (e.g. 'macha', 'semma') is fine but keep it readable."
    ),
}

SYSTEM_PROMPT = """You are the scriptwriter for a high-energy street-food YouTube Shorts vlogger.
You write a first-person vlog narration for a clearly AI-GENERATED character who "visits" a food shop.

Hard rules:
- Output ONLY valid JSON, no markdown fences.
- Do NOT invent hard facts about the real shop: no made-up prices, awards, owner names, hygiene claims,
  opening hours or health claims. Describe flavour, texture, aroma and vibe in general, enthusiastic terms.
- Only name dishes the user listed. If none were listed, talk about "the signature items" in general terms.
- Keep total spoken length around {target_words} words (about {seconds} seconds).
- Tone: casual, enthusiastic, descriptive, talks directly to the camera ("guys", "friends"), reacts while eating.
- Never claim to be a real person who really ate the food."""

USER_PROMPT = """Shop: {name}
Area: {area}
Dishes to feature: {dishes}
{language_rule}

Return JSON exactly in this shape:
{{
  "title": "catchy video title under 70 characters",
  "rating": 4.3,
  "hashtags": ["#foodreview", "#streetfood"],
  "scenes": [
    {{"id": "intro",    "text": "hook + greet the viewers + introduce the shop and area (2-3 sentences)", "emotion": "excited"}},
    {{"id": "entering", "text": "walking in, first look at the place, ordering (2 sentences)", "emotion": "curious"}},
    {{"id": "tasting",  "text": "first bite reactions, texture, spice, aroma, comparing dishes (4-5 sentences)", "emotion": "delighted"}},
    {{"id": "verdict",  "text": "honest verdict, one small suggestion, rating out of 5 (2-3 sentences)", "emotion": "happy"}},
    {{"id": "outro",    "text": "call to action: like, share, subscribe, tell us where to go next (1-2 sentences)", "emotion": "excited"}}
  ]
}}"""


@dataclass
class Scene:
    id: str
    text: str
    emotion: str = "happy"


@dataclass
class ReviewScript:
    title: str
    rating: float
    hashtags: list[str]
    scenes: list[Scene]
    generator: str = "llm"

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def full_text(self) -> str:
        return " ".join(s.text for s in self.scenes)


def generate_script(shop: ShopInfo, language: str, dishes: str = "") -> ReviewScript:
    language = language if language in LANGUAGE_RULES else "tamil"
    if LLM_API_KEY:
        try:
            return _llm_script(shop, language, dishes)
        except Exception as exc:  # noqa: BLE001 - fall back on any failure
            print(f"[script_generator] LLM failed ({exc!r}); using template script")
    return _template_script(shop, language, dishes)


# --------------------------------------------------------------------------- LLM
def _llm_script(shop: ShopInfo, language: str, dishes: str) -> ReviewScript:
    seconds = min(MAX_SCRIPT_SECONDS, 60)
    target_words = int(seconds * (2.1 if language == "english" else 1.6))
    payload = {
        "model": LLM_MODEL,
        "temperature": 0.85,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT.format(target_words=target_words, seconds=seconds)},
            {
                "role": "user",
                "content": USER_PROMPT.format(
                    name=shop.name,
                    area=shop.area or "not specified",
                    dishes=dishes or "not specified",
                    language_rule=LANGUAGE_RULES[language],
                ),
            },
        ],
    }
    resp = httpx.post(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {LLM_API_KEY}"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    return _parse_llm_json(content)


def _parse_llm_json(content: str) -> ReviewScript:
    content = content.strip()
    content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.M).strip()
    data = json.loads(content)
    by_id = {s.get("id"): s for s in data.get("scenes", []) if s.get("text")}
    missing = [i for i in SCENE_IDS if i not in by_id]
    if missing:
        raise ValueError(f"LLM output missing scenes: {missing}")
    scenes = [Scene(id=i, text=str(by_id[i]["text"]).strip(), emotion=str(by_id[i].get("emotion", "happy"))) for i in SCENE_IDS]
    try:
        rating = max(1.0, min(5.0, float(data.get("rating", 4.2))))
    except (TypeError, ValueError):
        rating = 4.2
    tags = [t if t.startswith("#") else f"#{t}" for t in data.get("hashtags", [])][:8] or ["#foodreview", "#streetfood"]
    return ReviewScript(title=str(data.get("title", "Food Review"))[:90], rating=round(rating, 1), hashtags=tags, scenes=scenes)


# ---------------------------------------------------------------------- fallback
def _template_script(shop: ShopInfo, language: str, dishes: str) -> ReviewScript:
    name, area = shop.name, shop.area
    where_en = f" in {area}" if area else ""
    where_ta = f" {area} பகுதியில்" if area else ""
    dish_list = [d.strip() for d in re.split(r"[,/&]| and ", dishes) if d.strip()]
    first = dish_list[0] if dish_list else ""

    if language == "english":
        food_line = f"I ordered the {first}" if first else "I ordered their signature items"
        scenes = [
            Scene("intro", f"Hey foodies, welcome back! Today we are checking out {name}{where_en}. The hype around this place is real, so let's see if it deserves it!", "excited"),
            Scene("entering", f"Okay guys, we're walking in right now. Look at the vibe, the aroma is already hitting me. {food_line}, and it's coming out hot!", "curious"),
            Scene("tasting", f"Alright, first bite... oh wow! The flavour is bold, the texture is spot on, and you can feel the freshness. Let me take another bite, because this is seriously good. Friends, this is exactly the kind of food that makes your day better.", "delighted"),
            Scene("verdict", f"My honest verdict? Totally worth a visit. Maybe go a little early before the crowd arrives. I'm giving {name} a solid four point four out of five!", "happy"),
            Scene("outro", "If you enjoyed this review, hit like, share it with your food buddy and subscribe. Tell me in the comments which shop I should visit next. See you soon, bye!", "excited"),
        ]
        title = f"{name} Food Review - Is It Worth It?"
    else:
        food_line = f"{first} ஆர்டர் பண்ணிருக்கேன்" if first else "அவங்க ஸ்பெஷல் ஐட்டம்ஸ் ஆர்டர் பண்ணிருக்கேன்"
        scenes = [
            Scene("intro", f"வணக்கம் ஃப்ரெண்ட்ஸ்! இன்னைக்கு நாம வந்திருக்கறது {name}{where_ta}. இந்த கடையைப் பத்தி நிறைய பேர் பேசுறாங்க, அதான் நேரா வந்துட்டேன்!", "excited"),
            Scene("entering", f"இதோ கடைக்குள்ள போயிட்டு இருக்கேன். வாசனையே ஆளை இழுக்குது! {food_line}, சூடா வருது பாருங்க!", "curious"),
            Scene("tasting", "சரி, முதல் வாய்... அடடா! சுவை செமயா இருக்கு, பதம் கரெக்டா இருக்கு, ஃப்ரெஷ்ஷா இருக்குன்னு தெரியுது. இன்னொரு வாய் சாப்பிட்டுப் பாக்கறேன், உண்மையிலேயே சூப்பர் ஃப்ரெண்ட்ஸ்!", "delighted"),
            Scene("verdict", f"என்னோட நேர்மையான கருத்து, கண்டிப்பா ஒரு தடவை போய்ப் பாக்கலாம். கூட்டம் வர்றதுக்கு முன்னாடி போனா நல்லது. {name}க்கு நான் ஐந்துக்கு நான்கு புள்ளி நான்கு கொடுக்கறேன்!", "happy"),
            Scene("outro", "வீடியோ பிடிச்சிருந்தா லைக் பண்ணுங்க, ஷேர் பண்ணுங்க, சப்ஸ்க்ரைப் பண்ணுங்க. அடுத்து எந்தக் கடைக்குப் போகணும்னு கமெண்ட்ல சொல்லுங்க. சீக்கிரம் சந்திப்போம்!", "excited"),
        ]
        title = f"{name} Food Review | சுவை எப்படி?"

    return ReviewScript(title=title, rating=4.4, hashtags=["#foodreview", "#streetfood", "#foodvlog", "#aigenerated"], scenes=scenes, generator="template")
