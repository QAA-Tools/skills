import argparse
import json
import os
from contextlib import contextmanager
from pathlib import Path

from stage3_llm_core import fake_generate_one_paper, generate_one_paper
from topic_stage2_score_and_enrich import compute_topic_match_score, current_shanghai_timestamp, read_jsonl


@contextmanager
def temporary_env(name: str, value: str | None):
    original = os.environ.get(name)
    try:
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
        yield
    finally:
        if original is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = original


def load_stage3_config(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ["topic_query", "stage2_final", "outdir", "selected_n", "llm_mode"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"missing required config fields: {', '.join(missing)}")
    topic_query = str(config.get("topic_query") or "").strip()
    if not topic_query:
        raise ValueError("topic_query must not be empty")
    stage2_final = Path(config["stage2_final"])
    if not stage2_final.exists():
        raise ValueError("stage2_final does not exist")
    outdir = Path(config["outdir"])
    selected_n = int(config["selected_n"])
    if not (1 <= selected_n <= 20):
        raise ValueError("selected_n must be between 1 and 20")
    process_batch_limit = int(config.get("process_batch_limit") or 5)
    if process_batch_limit <= 0:
        raise ValueError("process_batch_limit must be > 0")
    llm_mode = str(config.get("llm_mode") or "").strip().lower()
    if llm_mode not in {"fake", "api", "auto"}:
        raise ValueError("llm_mode must be one of: fake, api, auto")
    return {
        "topic_query": topic_query,
        "stage2_final": str(stage2_final),
        "outdir": str(outdir),
        "selected_n": selected_n,
        "process_batch_limit": process_batch_limit,
        "llm_mode": llm_mode,
        "write_debug_artifacts": bool(config.get("write_debug_artifacts", True)),
        "content_model": str(config.get("content_model") or os.environ.get("OPENAI_MODEL", "gpt-5.5")),
    }


def normalize_stage2_row(row: dict, *, index: int, topic_query: str) -> dict:
    title_en = str(row.get("paper_title") or row.get("title") or "").strip()
    abstract_en = str(row.get("paper_abstract") or row.get("abstract") or "").strip()
    score_ai = float(row.get("score_journal_ai") or 0.0)
    score_impact = float(row.get("score_journal_impact") or 0.0)
    return {
        "source_row": dict(row),
        "source_index": index,
        "record_id": str(row.get("paper_id") or row.get("record_id") or f"row_{index}"),
        "title_en": title_en,
        "abstract_en": abstract_en,
        "doi": str(row.get("paper_doi") or row.get("doi") or "").strip(),
        "paper_url": str(row.get("paper_url") or "").strip(),
        "journal": str(row.get("paper_journal") or row.get("journal") or "").strip(),
        "publication_date": str(row.get("publication_date") or "").strip(),
        "authors": list(row.get("authors") or []),
        "journal_score": max(score_ai, score_impact),
        "topic_match_score": compute_topic_match_score(topic_query, title_en, abstract_en),
        "video_done": bool(row.get("video_done")),
    }


def sort_and_select_rows(rows: list[dict], selected_n: int) -> tuple[list[dict], list[dict]]:
    candidates = [row for row in rows if row.get("title_en") and not row.get("video_done")]
    candidates.sort(key=lambda row: (-float(row.get("journal_score") or 0.0), -float(row.get("topic_match_score") or 0.0), int(row.get("source_index") or 0)))
    selected = candidates[:selected_n]
    selected_ids = {row["record_id"] for row in selected}
    other = [row for row in candidates if row["record_id"] not in selected_ids]
    return selected, other


def build_other_papers(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        title_en = str(row.get("title_en") or "").strip()
        if not title_en:
            continue
        out.append(
            {
                "record_id": row.get("record_id") or "",
                "title_en": title_en,
                "title_zh": "",
                "doi": row.get("doi") or "",
            }
        )
    return out


def _clean_text(value) -> str:
    return str(value or "").strip()


def _clean_lines(value) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item).strip()]


def row_has_stage3_content(row: dict) -> bool:
    return bool(
        _clean_text(row.get("content_brief"))
        and _clean_lines(row.get("content_key_points"))
        and _clean_text(row.get("content_title_zh"))
        and _clean_text(row.get("content_voice_intro"))
        and _clean_lines(row.get("content_voice_points"))
    )


def paper_has_complete_content(paper: dict) -> bool:
    return bool(
        _clean_text(paper.get("brief"))
        and _clean_lines(paper.get("key_points"))
        and _clean_text(paper.get("title_zh"))
        and _clean_text(paper.get("voice_intro"))
        and _clean_lines(paper.get("voice_points"))
    )


def load_existing_stage3_output(path: Path) -> dict:
    if not path.exists():
        return {"papers": [], "other_papers": [], "failed_papers": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"papers": [], "other_papers": [], "failed_papers": []}
    return {
        "topic_query": data.get("topic_query") or "",
        "selected_n": data.get("selected_n") or 0,
        "generated_at": data.get("generated_at") or "",
        "papers": list(data.get("papers") or []),
        "other_papers": list(data.get("other_papers") or []),
        "failed_papers": list(data.get("failed_papers") or []),
    }


def build_paper_from_existing(paper: dict, normalized_row: dict) -> dict:
    return {
        "record_id": normalized_row.get("record_id") or paper.get("record_id") or "",
        "title_en": normalized_row.get("title_en") or paper.get("title_en") or "",
        "title_zh": _clean_text(paper.get("title_zh")),
        "doi": normalized_row.get("doi") or paper.get("doi") or "",
        "paper_url": normalized_row.get("paper_url") or paper.get("paper_url") or "",
        "brief": _clean_text(paper.get("brief")) or _clean_text(paper.get("voice_intro")),
        "key_points": _clean_lines(paper.get("key_points")),
        "voice_intro": _clean_text(paper.get("voice_intro")) or _clean_text(paper.get("brief")),
        "voice_points": _clean_lines(paper.get("voice_points")),
    }


def failure_entry_blocks_retry(entry: dict) -> bool:
    return bool(_clean_text(entry.get("failure_reason")))


def build_failure_entry(row: dict, exc: Exception) -> dict:
    return {
        "record_id": row.get("record_id") or "",
        "title_en": row.get("title_en") or "",
        "doi": row.get("doi") or "",
        "failure_stage": parse_failure_stage(exc),
        "failure_reason": str(exc) or exc.__class__.__name__,
    }


def build_paper_from_pool_row(source_row: dict, normalized_row: dict) -> dict:
    return {
        "record_id": normalized_row.get("record_id") or "",
        "title_en": normalized_row.get("title_en") or "",
        "title_zh": _clean_text(source_row.get("content_title_zh")),
        "doi": normalized_row.get("doi") or "",
        "paper_url": normalized_row.get("paper_url") or "",
        "brief": _clean_text(source_row.get("content_brief")) or _clean_text(source_row.get("content_voice_intro")),
        "key_points": _clean_lines(source_row.get("content_key_points")),
        "voice_intro": _clean_text(source_row.get("content_voice_intro")) or _clean_text(source_row.get("content_brief")),
        "voice_points": _clean_lines(source_row.get("content_voice_points")),
    }


def apply_generated_paper_to_pool_row(source_row: dict, paper: dict, *, now_ts: str, model_name: str) -> dict:
    updated = dict(source_row)
    updated["content_brief"] = _clean_text(paper.get("brief"))
    updated["content_brief_updated_at"] = now_ts
    updated["content_brief_model"] = model_name
    updated["content_key_points"] = _clean_lines(paper.get("key_points"))
    updated["content_key_points_updated_at"] = now_ts
    updated["content_key_points_model"] = model_name
    updated["content_title_zh"] = _clean_text(paper.get("title_zh"))
    updated["content_title_zh_updated_at"] = now_ts
    updated["content_title_zh_model"] = model_name
    updated["content_voice_intro"] = _clean_text(paper.get("voice_intro"))
    updated["content_voice_intro_updated_at"] = now_ts
    updated["content_voice_intro_model"] = model_name
    updated["content_voice_points"] = _clean_lines(paper.get("voice_points"))
    updated["content_voice_points_updated_at"] = now_ts
    updated["content_voice_points_model"] = model_name
    updated["audit_updated_at"] = now_ts
    updated["audit_last_stage"] = "stage3_content_generated"
    return updated


def choose_generate_one_paper_fn(llm_mode: str, generate_one_paper_fn=None):
    if generate_one_paper_fn is not None:
        return generate_one_paper_fn
    if llm_mode == "fake":
        return fake_generate_one_paper
    return generate_one_paper


def parse_failure_stage(exc: Exception) -> str:
    message = str(exc).lower()
    for stage in ["page", "voice", "title"]:
        if stage in message:
            return stage
    return "unknown"


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_stage3(config: dict, *, generate_one_paper_fn=None) -> dict:
    config = dict(config)
    config.setdefault("process_batch_limit", 5)
    rows = read_jsonl(config["stage2_final"])
    normalized = [normalize_stage2_row(row, index=i, topic_query=config["topic_query"]) for i, row in enumerate(rows)]
    selected, other = sort_and_select_rows(normalized, config["selected_n"])
    outdir = Path(config["outdir"])
    outdir.mkdir(parents=True, exist_ok=True)
    out_path = outdir / "input_stage3.json"
    existing_output = load_existing_stage3_output(out_path)
    existing_papers_by_id = {str(item.get("record_id") or ""): dict(item) for item in existing_output.get("papers", []) if str(item.get("record_id") or "")}
    existing_failed_by_id = {
        str(item.get("record_id") or ""): dict(item) for item in existing_output.get("failed_papers", []) if str(item.get("record_id") or "")
    }
    if config.get("write_debug_artifacts", True):
        write_json(
            outdir / "stage3_selected_preview.json",
            {
                "topic_query": config["topic_query"],
                "selected_record_ids": [row["record_id"] for row in selected],
                "selected_rows": [
                    {
                        "record_id": row["record_id"],
                        "title_en": row["title_en"],
                        "journal_score": row["journal_score"],
                        "topic_match_score": row["topic_match_score"],
                    }
                    for row in selected
                ],
            },
        )
    generate_impl = choose_generate_one_paper_fn(config["llm_mode"], generate_one_paper_fn)
    papers = []
    failed_papers = []
    process_slots_left = int(config.get("process_batch_limit") or 5)
    processed_this_run = 0
    debug_log_path = outdir / "api_debug.jsonl" if config.get("write_debug_artifacts", True) and config["llm_mode"] in {"api", "auto"} else None
    if debug_log_path and debug_log_path.exists():
        debug_log_path.unlink()
    with temporary_env("PRL_API_DEBUG_LOG", str(debug_log_path) if debug_log_path else None):
        for row in selected:
            record_id = row["record_id"]
            existing_paper = existing_papers_by_id.get(record_id)
            existing_failed = existing_failed_by_id.get(record_id)
            if existing_paper and paper_has_complete_content(existing_paper):
                papers.append(build_paper_from_existing(existing_paper, row))
                continue
            if existing_failed and failure_entry_blocks_retry(existing_failed):
                failed_papers.append(existing_failed)
                continue
            if process_slots_left <= 0:
                continue
            try:
                paper = generate_impl(dict(row), model_name=config["content_model"])
                paper = build_paper_from_existing(paper, row)
                papers.append(paper)
                existing_papers_by_id[record_id] = paper
                existing_failed_by_id.pop(record_id, None)
            except Exception as exc:
                failed = build_failure_entry(row, exc)
                failed_papers.append(failed)
                existing_failed_by_id[record_id] = failed
            processed_this_run += 1
            process_slots_left -= 1
    remaining_to_process = 0
    for row in selected:
        record_id = row["record_id"]
        existing_paper = existing_papers_by_id.get(record_id)
        existing_failed = existing_failed_by_id.get(record_id)
        if existing_paper and paper_has_complete_content(existing_paper):
            continue
        if existing_failed and failure_entry_blocks_retry(existing_failed):
            continue
        remaining_to_process += 1
    status = "OK_NO_REMAINING" if remaining_to_process == 0 else "OK_PROGRESS"
    if not papers:
        raise RuntimeError("stage3 generation produced 0 papers succeeded")
    failed_output = []
    selected_ids = {row["record_id"] for row in selected}
    for row in selected:
        record_id = row["record_id"]
        if record_id in existing_failed_by_id and not (record_id in existing_papers_by_id and paper_has_complete_content(existing_papers_by_id[record_id])):
            failed_output.append(existing_failed_by_id[record_id])
    result = {
        "topic_query": config["topic_query"],
        "selected_n": config["selected_n"],
        "generated_at": current_shanghai_timestamp(),
        "papers": [existing_papers_by_id[row["record_id"]] for row in selected if row["record_id"] in existing_papers_by_id and paper_has_complete_content(existing_papers_by_id[row["record_id"]])],
        "other_papers": build_other_papers(other),
        "failed_papers": failed_output,
    }
    write_json(out_path, result)
    return {
        "status": status,
        "stage2_rows": len(rows),
        "selected": len(selected),
        "processed_this_run": processed_this_run,
        "generated": len(result["papers"]),
        "failed": len(result["failed_papers"]),
        "remaining_to_process": remaining_to_process,
        "other_papers": len(result["other_papers"]),
        "out": str(out_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_stage3_config(args.config)
    summary = run_stage3(config)
    print(f"status={summary['status']}")
    print(f"stage2_rows={summary['stage2_rows']}")
    print(f"selected={summary['selected']}")
    print(f"processed_this_run={summary['processed_this_run']}")
    print(f"generated={summary['generated']}")
    print(f"remaining_to_process={summary['remaining_to_process']}")
    print(f"other_papers={summary['other_papers']}")
    print(f"out={summary['out']}")


if __name__ == "__main__":
    main()
