#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from prl_llm_core import build_daily_raw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--feed-n", type=int, default=25)
    ap.add_argument("--days-ago", type=int, default=0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    raw = build_daily_raw(selected_n=args.n, recent_n=args.feed_n, days_ago=args.days_ago)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(out_path))


if __name__ == "__main__":
    main()
