#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
import subprocess
import sys

RENDER_PRL = Path(__file__).with_name("render_prl.py")
RENDER_COVER = Path(__file__).with_name("render_prl_bilibili_cover.py")


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--cover-out", default="")
    ap.add_argument("--tags-file", default="")
    args = ap.parse_args()

    input_path = Path(args.input)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    run([sys.executable, str(RENDER_PRL), "--input", str(input_path), "--outdir", str(outdir)])

    data = json.loads(input_path.read_text(encoding="utf-8"))
    date = (data.get("date") or "").strip()
    if not date:
        raise SystemExit("input.json missing date")

    cover_out = Path(args.cover_out) if args.cover_out else (outdir / "cover.png")
    run([
        sys.executable,
        str(RENDER_COVER),
        "--date",
        date,
        "--input-json",
        str(input_path),
        "--tags-file",
        args.tags_file,
        "--out",
        str(cover_out),
    ])

    print(str(outdir / "out.mp4"))
    print(str(cover_out))


if __name__ == "__main__":
    main()
