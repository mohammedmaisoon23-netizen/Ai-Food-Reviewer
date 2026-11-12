"""End-to-end orchestration for one review: parse input -> script -> TTS -> video."""
import json
import shutil
import traceback

import database as db
from config import OUTPUT_DIR, WORK_DIR
from services import tts_service
from services.characters import get_character
from services.place_parser import parse_shop_input
from services.script_generator import generate_script
from services.video_assembler import assemble_video


def run_pipeline(rid: str) -> None:
    review = db.get_review(rid)
    if not review:
        return
    work = WORK_DIR / rid
    out_dir = OUTPUT_DIR / rid
    warnings: list[str] = []

    def step(pct: int, stage: str, **extra) -> None:
        db.update_review(rid, status="running", progress=pct, stage=stage, **extra)

    try:
        step(3, "Reading shop details")
        shop = parse_shop_input(review["shop_input"])
        warnings += shop.notes
        db.update_review(rid, shop_name=shop.name, shop_area=shop.area)

        step(10, "Writing the vlog script")
        script = generate_script(shop, review["language"], review["dishes"] or "")
        if script.generator == "template":
            warnings.append("Used the built-in template script (no GROQ_API_KEY set or the LLM call failed).")
        step(30, "Script ready", script_json=json.dumps({**script.to_dict(), "warnings": warnings}, ensure_ascii=False))

        step(32, "Generating voiceover")
        char = get_character(review["character"])
        audio = tts_service.synthesize_scenes(script.scenes, review["language"], char.gender, work / "tts")
        engines = getattr(tts_service.synthesize_scenes, "last_engines", [])
        if "silent" in engines:
            warnings.append("No TTS engine was reachable, so the video has no voice. Check the server's internet access.")
        elif "espeak" in engines:
            warnings.append("Used the offline espeak voice because Edge-TTS/gTTS were unreachable.")
        elif "gtts" in engines:
            warnings.append("Edge-TTS was unavailable; used Google TTS for some or all scenes.")
        step(62, "Voiceover ready", script_json=json.dumps({**script.to_dict(), "warnings": warnings}, ensure_ascii=False))

        out_dir.mkdir(parents=True, exist_ok=True)
        result = assemble_video(
            shop_name=shop.name, shop_area=shop.area, title=script.title, rating=script.rating,
            scenes=script.scenes, scene_audio=audio, character_id=review["character"],
            work_dir=work, out_path=out_dir / "review.mp4",
            progress=lambda p, s: step(p, s),
        )
        if result["talking_head"] is False and _talking_head_configured():
            warnings.append("Talking-head engine failed; used the built-in animated avatar instead.")

        db.update_review(
            rid, status="done", progress=100, stage="Done",
            audio_path=str(result["audio"]), video_path=str(result["video"]),
            script_json=json.dumps({**script.to_dict(), "warnings": warnings, "duration": result["duration"]}, ensure_ascii=False),
        )
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        db.update_review(rid, status="failed", stage="Failed", error=f"{type(exc).__name__}: {exc}"[:500])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _talking_head_configured() -> bool:
    from config import TALKING_HEAD_CMD, TALKING_HEAD_URL
    return bool(TALKING_HEAD_CMD or TALKING_HEAD_URL)
