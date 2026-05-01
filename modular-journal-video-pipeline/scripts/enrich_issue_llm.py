#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path

from prl_llm_core import (
    build_llm_prompt,
    build_publish_desc,
    build_publish_tags,
    generate_input_json,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt-out", default="")
    ap.add_argument("--desc-out", default="")
    ap.add_argument("--tags-out", default="")
    ap.add_argument("--selected-n", type=int, default=8)
    args = ap.parse_args()

    raw_path = Path(args.raw)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))

    prompt = build_llm_prompt(raw, args.selected_n)
    prompt_out = Path(args.prompt_out) if args.prompt_out else raw_path.with_name("llm_prompt.txt")
    prompt_out.parent.mkdir(parents=True, exist_ok=True)
    prompt_out.write_text(prompt, encoding="utf-8")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    api_debug_log = out_path.with_name("api_debug.jsonl")
    if api_debug_log.exists():
        api_debug_log.unlink()
    os.environ["PRL_API_DEBUG_LOG"] = str(api_debug_log)
    generated, mode = generate_input_json(raw, prompt, args.selected_n)
    out_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    desc_out = Path(args.desc_out) if args.desc_out else out_path.with_name("publish_desc.txt")
    tags_out = Path(args.tags_out) if args.tags_out else out_path.with_name("publish_tags.txt")
    desc_out.write_text(build_publish_desc(generated, raw), encoding="utf-8")
    tags_out.write_text(build_publish_tags(generated), encoding="utf-8")

    print(str(out_path))
    print(f"mode={mode}")
    print(f"prompt={prompt_out}")
    print(f"desc={desc_out}")
    print(f"tags={tags_out}")


if __name__ == "__main__":
    main()
