"""Command-line runner: the same pipeline as the web app, no server. Used by GitHub Actions.

  python cli.py --shop "Ambur Star Biryani, Anna Nagar" --dishes "biryani" --language tamil --character arun --out ../output
  python cli.py --batch ../shops.txt --out ../output        (one shop per line: "name, area | dish1, dish2")
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _slug(text: str) -> str:
    keep = "".join(c.lower() if c.isalnum() else "-" for c in text)
    return "-".join(p for p in keep.split("-") if p)[:40] or "review"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", default="")
    ap.add_argument("--dishes", default="")
    ap.add_argument("--language", default="tamil", choices=["tamil", "english"])
    ap.add_argument("--character", default="arun")
    ap.add_argument("--batch", default="", help="text file, one 'shop | dishes' per line")
    ap.add_argument("--out", default="output")
    args = ap.parse_args()

    jobs: list[tuple[str, str]] = []
    if args.batch:
        for line in Path(args.batch).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                shop, _, dishes = line.partition("|")
                jobs.append((shop.strip(), dishes.strip()))
    elif args.shop.strip():
        jobs.append((args.shop.strip(), args.dishes.strip()))
    if not jobs:
        print("Nothing to do: give --shop or --batch.", file=sys.stderr)
        return 2

    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="reviewer-")
    import database as db  # imported after DATA_DIR is set
    from pipeline import run_pipeline

    db.init_db()
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    summary = ["## AI Food Reviewer results\n"]
    failures = 0

    for shop, dishes in jobs:
        rid = db.create_review(shop, dishes, args.language, args.character)
        print(f"\n=== {shop} ===", flush=True)
        run_pipeline(rid)
        r = db.get_review(rid)
        if r["status"] != "done":
            failures += 1
            summary.append(f"- FAILED **{shop}**: {r['error']}\n")
            print("FAILED:", r["error"], file=sys.stderr)
            continue
        dest = out_root / _slug(r["shop_name"] or shop)
        dest.mkdir(parents=True, exist_ok=True)
        for f in Path(r["video_path"]).parent.glob("review.*"):
            shutil.copy(f, dest / f.name)
        (dest / "script.json").write_text(json.dumps(r["script"], ensure_ascii=False, indent=2), encoding="utf-8")
        sc = r["script"] or {}
        summary.append(f"- OK **{r['shop_name']}** - {sc.get('title', '')} ({sc.get('duration', '?')}s, rating {sc.get('rating', '?')})\n")
        for w in sc.get("warnings", []):
            summary.append(f"  - WARNING: {w}\n")

    (out_root / "summary.md").write_text("".join(summary), encoding="utf-8")
    print("".join(summary))
    return 1 if failures == len(jobs) else 0


if __name__ == "__main__":
    sys.exit(main())
