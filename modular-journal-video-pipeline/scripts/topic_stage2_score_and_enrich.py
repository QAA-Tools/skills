import argparse
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from abstract_enricher import enrich_abstract_from_title
from journal_score_dict import (
    extract_journal_score_payload,
    get_or_fetch_journal_score_payload,
    normalize_journal_name,
    read_journal_dict,
    round_score_value,
)
from journal_scorer import score_journal_once
from openalex_client import parse_openalex_work
from runtime_logger import log_runtime_event

TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z\u4e00-\u9fff]+")
TIMESTAMPED_OPENALEX_RE = re.compile(r"openalex_(.+)\.jsonl$")


def current_shanghai_timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def read_jsonl(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def extract_openalex_batch_ts(path: str | Path) -> str:
    match = TIMESTAMPED_OPENALEX_RE.search(Path(path).name)
    if not match:
        raise ValueError("openalex input filename must include timestamp like openalex_YYYY-MM-DDTHH-MM-SS.jsonl")
    return match.group(1)


def stable_paper_id(title_normalized: str) -> str:
    digest = hashlib.sha1((title_normalized or "").encode("utf-8")).hexdigest()[:12]
    return f"paper_{digest}"


def unique_extend(existing: list[str] | None, incoming: list[str] | None) -> list[str]:
    out = list(existing or [])
    for item in incoming or []:
        if item and item not in out:
            out.append(item)
    return out


def build_pool_record(work: dict, *, batch_ts: str, retrieved_at: str) -> dict | None:
    if str(work.get("discard_reason") or "").strip():
        return None

    parsed = parse_openalex_work(
        work,
        topic_query="",
        start_date="1900-01-01",
        end_date="2100-12-31",
        retrieved_at=retrieved_at,
    )
    if parsed is None:
        return None

    paper_title_normalized = parsed["title_normalized"]
    return {
        "paper_id": stable_paper_id(paper_title_normalized),
        "paper_title": parsed["title"],
        "paper_title_normalized": paper_title_normalized,
        "paper_abstract": parsed["abstract"],
        "paper_abstract_source": "openalex" if parsed["abstract"] else "",
        "paper_abstract_lookup_status": "not_needed" if parsed["abstract"] else "",
        "paper_abstract_lookup_reason": "",
        "paper_abstract_lookup_updated_at": retrieved_at if parsed["abstract"] else "",
        "paper_publication_date": parsed["publication_date"],
        "paper_journal": parsed["journal"],
        "paper_doi": parsed["doi"],
        "paper_url": parsed["paper_url"],
        "paper_authors": list(parsed.get("authors") or []),
        "paper_first_author": parsed.get("first_author") or "",
        "source_origin": "openalex",
        "source_openalex_id": parsed.get("openalex_id") or "",
        "source_url": parsed.get("source_url") or "",
        "source_raw_ids": list(parsed.get("raw_source_ids") or []),
        "source_openalex_batch_ts": [batch_ts],
        "pool_created_at": retrieved_at,
        "pool_updated_at": retrieved_at,
        "pool_duplicate_count": int(parsed.get("raw_duplicate_count") or 1),
        "score_journal_ai": None,
        "score_journal_ai_samples": [],
        "score_journal_ai_model": "",
        "score_journal_ai_updated_at": "",
        "score_journal_impact": None,
        "score_journal_impact_samples": [],
        "score_journal_impact_model": "",
        "score_journal_impact_updated_at": "",
        "score_candidate_passed": False,
        "score_candidate_updated_at": "",
        "content_brief": "",
        "content_brief_updated_at": "",
        "content_brief_model": "",
        "content_key_points": [],
        "content_key_points_updated_at": "",
        "content_key_points_model": "",
        "content_title_zh": "",
        "content_title_zh_updated_at": "",
        "content_title_zh_model": "",
        "content_voice_intro": "",
        "content_voice_intro_updated_at": "",
        "content_voice_intro_model": "",
        "content_voice_points": [],
        "content_voice_points_updated_at": "",
        "content_voice_points_model": "",
        "video_done": False,
        "video_done_at": "",
        "video_bvid": "",
        "audit_created_at": retrieved_at,
        "audit_updated_at": retrieved_at,
        "audit_last_stage": "stage2_pool_ingest",
    }


def merge_pool_record(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    if not merged.get("paper_abstract") and incoming.get("paper_abstract"):
        merged["paper_abstract"] = incoming["paper_abstract"]
        merged["paper_abstract_source"] = incoming.get("paper_abstract_source") or "openalex"
        merged["paper_abstract_lookup_status"] = incoming.get("paper_abstract_lookup_status") or "not_needed"
        merged["paper_abstract_lookup_reason"] = incoming.get("paper_abstract_lookup_reason") or ""
        merged["paper_abstract_lookup_updated_at"] = incoming.get("paper_abstract_lookup_updated_at") or incoming.get("pool_updated_at") or ""
    if not merged.get("paper_journal") and incoming.get("paper_journal"):
        merged["paper_journal"] = incoming["paper_journal"]
    if not merged.get("paper_doi") and incoming.get("paper_doi"):
        merged["paper_doi"] = incoming["paper_doi"]
    if not merged.get("paper_url") and incoming.get("paper_url"):
        merged["paper_url"] = incoming["paper_url"]
    if len(incoming.get("paper_authors") or []) > len(merged.get("paper_authors") or []):
        merged["paper_authors"] = list(incoming.get("paper_authors") or [])
        merged["paper_first_author"] = incoming.get("paper_first_author") or ""
    if not merged.get("source_url") and incoming.get("source_url"):
        merged["source_url"] = incoming["source_url"]
    if not merged.get("source_openalex_id") and incoming.get("source_openalex_id"):
        merged["source_openalex_id"] = incoming["source_openalex_id"]

    before_raw_ids = set(merged.get("source_raw_ids") or [])
    before_batch_ts = set(merged.get("source_openalex_batch_ts") or [])

    merged["source_raw_ids"] = unique_extend(merged.get("source_raw_ids"), incoming.get("source_raw_ids"))
    merged["source_openalex_batch_ts"] = unique_extend(merged.get("source_openalex_batch_ts"), incoming.get("source_openalex_batch_ts"))

    new_raw_id_count = len(set(merged["source_raw_ids"]) - before_raw_ids)
    new_batch_count = len(set(merged["source_openalex_batch_ts"]) - before_batch_ts)
    if new_raw_id_count or new_batch_count:
        merged["pool_duplicate_count"] = int(merged.get("pool_duplicate_count") or 0) + 1
    else:
        merged["pool_duplicate_count"] = max(int(merged.get("pool_duplicate_count") or 0), len(merged["source_raw_ids"]))

    if (
        set(merged["source_raw_ids"]) != before_raw_ids
        or set(merged["source_openalex_batch_ts"]) != before_batch_ts
    ):
        merged["pool_updated_at"] = incoming.get("pool_updated_at") or merged.get("pool_updated_at") or ""
        merged["audit_updated_at"] = merged["pool_updated_at"]
    merged["audit_last_stage"] = "stage2_pool_ingest"
    return merged


def summarize_samples(samples: list[float], *, score_kind: str = "ai") -> float | None:
    clean = [float(x) for x in samples]
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    if score_kind == "impact":
        counts: dict[float, int] = {}
        for value in clean:
            counts[value] = counts.get(value, 0) + 1
        max_count = max(counts.values())
        mode_values = sorted(value for value, count in counts.items() if count == max_count)
        if len(mode_values) == 1:
            return mode_values[0]
        ordered = sorted(clean)
        best_pair = (ordered[0], ordered[1])
        best_gap = abs(ordered[1] - ordered[0])
        for left, right in zip(ordered, ordered[1:]):
            gap = abs(right - left)
            if gap < best_gap:
                best_pair = (left, right)
                best_gap = gap
        return sum(best_pair) / 2
    if len(clean) <= 2:
        return sum(clean) / len(clean)
    trimmed = sorted(clean)[1:-1]
    return sum(trimmed) / len(trimmed)


def tokenize_topic_words(topic_query: str) -> list[str]:
    tokens = [token for token in TOKEN_SPLIT_RE.split((topic_query or "").lower()) if token]
    return [token for token in tokens if len(token) >= 2]


def tokenize_text_words(text: str) -> list[str]:
    return [token for token in TOKEN_SPLIT_RE.split((text or "").lower()) if token]


def prefix_similarity(word: str, candidate: str) -> float:
    if not word or not candidate:
        return 0.0
    if word == candidate:
        return 1.0
    compared_chars = min(len(word), len(candidate), 10)
    if compared_chars <= 0:
        return 0.0
    matches = 0
    for idx in range(compared_chars):
        if word[idx] != candidate[idx]:
            break
        matches += 1
    return matches / compared_chars


def score_topic_word(word: str, candidates: list[str]) -> float:
    best = 0.0
    for candidate in candidates:
        score = 1.0 if candidate == word else prefix_similarity(word, candidate)
        if score > best:
            best = score
            if best == 1.0:
                break
    return best


def compute_topic_match_score(topic_query: str, title: str, abstract: str) -> float:
    topic_words = tokenize_topic_words(topic_query)
    if not topic_words:
        return 0.0
    candidates = tokenize_text_words(f"{title or ''} {abstract or ''}")
    if not candidates:
        return 0.0
    scores = [score_topic_word(word, candidates) for word in topic_words]
    return sum(scores) / len(scores)


def load_pool(path: str | Path) -> list[dict]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    return read_jsonl(file_path)


def load_stage2_config(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    required = ["openalex_inputs", "pool_path", "journal_ai_threshold", "journal_impact_threshold"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"missing required config fields: {', '.join(missing)}")
    inputs = [str(Path(item)) for item in config.get("openalex_inputs") or []]
    if not inputs:
        raise ValueError("openalex_inputs must not be empty")
    for item in inputs:
        if not Path(item).exists():
            raise ValueError(f"openalex input does not exist: {item}")
        extract_openalex_batch_ts(item)
    pool_path = Path(config["pool_path"])
    config["openalex_inputs"] = inputs
    config["pool_path"] = str(pool_path)
    config["run_log_path"] = str(Path(config.get("run_log_path") or pool_path.with_name(pool_path.stem + ".run_log.jsonl")))
    config["progress_log_path"] = str(Path(config.get("progress_log_path") or pool_path.with_name(pool_path.stem + ".progress.jsonl")))
    config["journal_dict_path"] = str(Path(config.get("journal_dict_path") or pool_path.with_name(pool_path.stem + ".journal_dict.jsonl")))
    config["journal_ai_threshold"] = float(config["journal_ai_threshold"])
    config["journal_impact_threshold"] = float(config["journal_impact_threshold"])
    config["score_sample_count"] = int(config.get("score_sample_count", 5))
    if config["score_sample_count"] <= 0:
        raise ValueError("score_sample_count must be > 0")
    raw_batch_limit = config.get("score_batch_limit")
    if raw_batch_limit in (None, "", 0):
        config["score_batch_limit"] = None
    else:
        config["score_batch_limit"] = int(raw_batch_limit)
        if config["score_batch_limit"] <= 0:
            raise ValueError("score_batch_limit must be > 0")
    config["journal_ai_model"] = str(config.get("journal_ai_model") or os.environ.get("OPENAI_MODEL", "gpt-5.5"))
    config["journal_impact_model"] = str(config.get("journal_impact_model") or os.environ.get("OPENAI_MODEL", "gpt-5.5"))
    config["force_rescore"] = bool(config.get("force_rescore", False))
    config["enable_abstract_enrichment"] = bool(config.get("enable_abstract_enrichment", False))
    return config


def score_pool_record(row: dict, config: dict, *, score_journal_fn=None, now_ts: str) -> dict:
    scorer = score_journal_fn or score_journal_once
    scored = dict(row)
    journal_name = scored.get("paper_journal") or ""

    def maybe_sample(kind: str, value_key: str, samples_key: str, model_key: str, updated_key: str, model_name: str):
        if scored.get(value_key) is not None and not config.get("force_rescore", False):
            return
        if not journal_name:
            scored[value_key] = None
            scored[samples_key] = []
            scored[model_key] = model_name
            scored[updated_key] = now_ts
            return
        samples = [float(scorer(journal_name, kind, idx, model_name)) for idx in range(config["score_sample_count"])]
        scored[value_key] = round_score_value(summarize_samples(samples, score_kind=kind))
        scored[samples_key] = samples
        scored[model_key] = model_name
        scored[updated_key] = now_ts

    maybe_sample("ai", "score_journal_ai", "score_journal_ai_samples", "score_journal_ai_model", "score_journal_ai_updated_at", config["journal_ai_model"])
    maybe_sample("impact", "score_journal_impact", "score_journal_impact_samples", "score_journal_impact_model", "score_journal_impact_updated_at", config["journal_impact_model"])
    scored["score_candidate_passed"] = bool(
        (scored.get("score_journal_ai") is not None and scored["score_journal_ai"] >= config["journal_ai_threshold"])
        or (
            scored.get("score_journal_impact") is not None
            and scored["score_journal_impact"] >= config["journal_impact_threshold"]
        )
    )
    scored["score_candidate_updated_at"] = now_ts
    scored["audit_updated_at"] = now_ts
    scored["audit_last_stage"] = "stage2_pool_ingest"
    return scored


def needs_scoring(row: dict, config: dict) -> bool:
    if config.get("force_rescore", False):
        return True
    return row.get("score_journal_ai") is None or row.get("score_journal_impact") is None


def needs_abstract_enrichment(row: dict) -> bool:
    if (row.get("paper_abstract") or "").strip():
        return False
    if row.get("paper_abstract_lookup_status") == "not_found":
        return False
    return True


def apply_cached_journal_scores(row: dict, cached: dict, *, now_ts: str) -> dict:
    updated = dict(row)
    for key, value in cached.items():
        updated[key] = value
    updated["score_candidate_updated_at"] = now_ts
    updated["audit_updated_at"] = now_ts
    updated["audit_last_stage"] = "stage2_pool_ingest"
    return updated


def enrich_abstract_if_needed(row: dict, config: dict, *, enrich_abstract_fn, now_ts: str) -> tuple[dict, bool]:
    updated = dict(row)
    if not config.get("enable_abstract_enrichment", False):
        return updated, False
    if not needs_abstract_enrichment(updated):
        return updated, False
    if enrich_abstract_fn is None:
        return updated, False

    result = enrich_abstract_fn(updated, config)
    if result and (result.get("abstract") or "").strip():
        updated["paper_abstract"] = (result.get("abstract") or "").strip()
        updated["paper_abstract_source"] = (result.get("source") or "enriched").strip()
        updated["paper_abstract_lookup_status"] = "done"
        updated["paper_abstract_lookup_reason"] = ""
    else:
        updated["paper_abstract_source"] = updated.get("paper_abstract_source") or "missing"
        updated["paper_abstract_lookup_status"] = "not_found"
        updated["paper_abstract_lookup_reason"] = "Not found: no usable abstract source"
    updated["paper_abstract_lookup_updated_at"] = now_ts
    updated["audit_updated_at"] = now_ts
    updated["audit_last_stage"] = "stage2_pool_ingest"
    return updated, True


def row_needs_processing(row: dict, config: dict) -> bool:
    return needs_scoring(row, config) or needs_abstract_enrichment(row)


def append_stage2_run_log(run_log_path: str | Path, entry: dict) -> None:
    file_path = Path(run_log_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def log_stage2_progress(progress_log_path: str | Path | None, *, event: str, status: str = "", **fields) -> None:
    log_runtime_event(progress_log_path, source="stage2", event=event, status=status, **fields)


def ordered_pool_rows(pool_by_title: dict[str, dict]) -> list[dict]:
    return [pool_by_title[key] for key in sorted(pool_by_title.keys())]


def persist_stage2_state(pool_path: str | Path, pool_by_title: dict[str, dict]) -> None:
    write_jsonl(pool_path, ordered_pool_rows(pool_by_title))


def run_stage2(config: dict, *, score_journal_fn=None, enrich_abstract_fn=None) -> dict:
    config = dict(config)
    pool_path = Path(config["pool_path"])
    config.setdefault("run_log_path", str(pool_path.with_name(pool_path.stem + ".run_log.jsonl")))
    config.setdefault("progress_log_path", str(pool_path.with_name(pool_path.stem + ".progress.jsonl")))
    config.setdefault("journal_dict_path", str(pool_path.with_name(pool_path.stem + ".journal_dict.jsonl")))
    config.setdefault("score_batch_limit", None)
    config.setdefault("enable_abstract_enrichment", False)
    if score_journal_fn is None:
        score_journal_fn = score_journal_once
    if enrich_abstract_fn is None and config.get("enable_abstract_enrichment", False):
        enrich_abstract_fn = enrich_abstract_from_title
    now_ts = current_shanghai_timestamp()
    pool_rows = load_pool(config["pool_path"])
    pool_by_title = {row["paper_title_normalized"]: dict(row) for row in pool_rows}
    journal_dict_path = Path(config["journal_dict_path"])

    ingested_openalex_rows = 0
    for input_path in config["openalex_inputs"]:
        batch_ts = extract_openalex_batch_ts(input_path)
        raw_rows = read_jsonl(input_path)
        for raw in raw_rows:
            ingested_openalex_rows += 1
            record = build_pool_record(raw, batch_ts=batch_ts, retrieved_at=now_ts)
            if record is None:
                continue
            key = record["paper_title_normalized"]
            if key in pool_by_title:
                pool_by_title[key] = merge_pool_record(pool_by_title[key], record)
            else:
                pool_by_title[key] = record

    process_batch_limit = config.get("score_batch_limit")
    process_slots_left = process_batch_limit if process_batch_limit is not None else None
    processed_this_run = 0
    scored_this_run = 0
    failed_this_run = 0
    log_stage2_progress(
        config.get("progress_log_path"),
        event="run_started",
        status="running",
        pool_path=config["pool_path"],
        run_log_path=config["run_log_path"],
        score_batch_limit=config.get("score_batch_limit"),
        score_sample_count=config.get("score_sample_count"),
        ingested_openalex_rows=ingested_openalex_rows,
        existing_pool_rows=len(pool_rows),
    )
    persist_stage2_state(pool_path, pool_by_title)

    for title_key in sorted(pool_by_title.keys()):
        row = pool_by_title[title_key]
        row_needs_work = row_needs_processing(row, config)

        if not row_needs_work:
            continue

        if process_slots_left is not None and process_slots_left <= 0:
            continue

        log_stage2_progress(
            config.get("progress_log_path"),
            event="row_started",
            status="running",
            paper_id=row.get("paper_id") or "",
            paper_title=row.get("paper_title") or "",
            journal_name=row.get("paper_journal") or "",
            needs_scoring=needs_scoring(row, config),
            needs_abstract_enrichment=needs_abstract_enrichment(row),
            process_slots_left=process_slots_left,
        )
        try:
            row_processed = False
            if needs_scoring(row, config):
                journal_name = row.get("paper_journal") or ""
                payload = get_or_fetch_journal_score_payload(
                    journal_name,
                    journal_dict_path,
                    now_ts=now_ts,
                    score_sample_count=config["score_sample_count"],
                    journal_ai_model=config["journal_ai_model"],
                    journal_impact_model=config["journal_impact_model"],
                    score_journal_fn=score_journal_fn,
                    force_rescore=config.get("force_rescore", False),
                )
                row = apply_cached_journal_scores(row, payload or {}, now_ts=now_ts)
                row["score_candidate_passed"] = bool(
                    (row.get("score_journal_ai") is not None and row["score_journal_ai"] >= config["journal_ai_threshold"])
                    or (
                        row.get("score_journal_impact") is not None
                        and row["score_journal_impact"] >= config["journal_impact_threshold"]
                    )
                )
                scored_this_run += 1
                row_processed = True

            row, abstract_processed = enrich_abstract_if_needed(
                row,
                config,
                enrich_abstract_fn=enrich_abstract_fn,
                now_ts=now_ts,
            )
            row_processed = row_processed or abstract_processed

            if row_processed:
                row = score_pool_record(
                    row,
                    config,
                    score_journal_fn=score_journal_fn,
                    now_ts=now_ts,
                )

            if row_processed:
                processed_this_run += 1
                if process_slots_left is not None:
                    process_slots_left -= 1
            log_stage2_progress(
                config.get("progress_log_path"),
                event="row_finished",
                status="success",
                paper_id=row.get("paper_id") or "",
                paper_title=row.get("paper_title") or "",
                journal_name=row.get("paper_journal") or "",
                row_processed=row_processed,
                scored=bool(row.get("score_journal_ai") is not None and row.get("score_journal_impact") is not None),
                abstract_lookup_status=row.get("paper_abstract_lookup_status") or "",
                process_slots_left=process_slots_left,
            )
        except Exception as e:
            row = dict(row)
            row["audit_updated_at"] = now_ts
            row["audit_last_stage"] = "stage2_pool_ingest_failed"
            failed_this_run += 1
            log_stage2_progress(
                config.get("progress_log_path"),
                event="row_finished",
                status="failed",
                paper_id=row.get("paper_id") or "",
                paper_title=row.get("paper_title") or "",
                journal_name=row.get("paper_journal") or "",
                error_type=type(e).__name__,
                process_slots_left=process_slots_left,
            )

        pool_by_title[title_key] = row
        persist_stage2_state(pool_path, pool_by_title)

    final_rows = ordered_pool_rows(pool_by_title)
    journal_dict_rows = read_journal_dict(journal_dict_path)
    remaining_to_process = sum(1 for row in final_rows if row_needs_processing(row, config))
    status = "OK_NO_REMAINING" if remaining_to_process == 0 else "OK_PROGRESS"
    summary = {
        "status": status,
        "ingested_openalex_rows": ingested_openalex_rows,
        "pool_row_count": len(final_rows),
        "processed_this_run": processed_this_run,
        "scored_this_run": scored_this_run,
        "failed_this_run": failed_this_run,
        "remaining_to_process": remaining_to_process,
        "remaining_to_score": remaining_to_process,
        "pool_path": config["pool_path"],
        "run_log_path": config["run_log_path"],
        "journal_dict_path": config["journal_dict_path"],
        "journal_dict_row_count": len(journal_dict_rows),
    }
    append_stage2_run_log(
        config["run_log_path"],
        {
            "ts": now_ts,
            "status": status,
            "ingested_openalex_rows": ingested_openalex_rows,
            "pool_row_count": len(final_rows),
            "processed_this_run": processed_this_run,
            "scored_this_run": scored_this_run,
            "failed_this_run": failed_this_run,
            "remaining_to_process": remaining_to_process,
            "remaining_to_score": remaining_to_process,
            "score_batch_limit": config.get("score_batch_limit"),
            "score_sample_count": config.get("score_sample_count"),
            "pool_path": config["pool_path"],
            "journal_dict_path": config["journal_dict_path"],
            "journal_dict_row_count": len(journal_dict_rows),
        },
    )
    log_stage2_progress(
        config.get("progress_log_path"),
        event="run_completed",
        status=status,
        processed_this_run=processed_this_run,
        scored_this_run=scored_this_run,
        failed_this_run=failed_this_run,
        remaining_to_process=remaining_to_process,
        pool_row_count=len(final_rows),
        journal_dict_row_count=len(journal_dict_rows),
        pool_path=config["pool_path"],
        run_log_path=config["run_log_path"],
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_stage2_config(args.config)
    summary = run_stage2(config)
    print(f"status={summary['status']}")
    print(f"ingested_openalex_rows={summary['ingested_openalex_rows']}")
    print(f"pool_row_count={summary['pool_row_count']}")
    print(f"processed_this_run={summary['processed_this_run']}")
    print(f"scored_this_run={summary['scored_this_run']}")
    print(f"remaining_to_process={summary['remaining_to_process']}")
    print(f"pool_path={summary['pool_path']}")
    print(f"run_log_path={summary['run_log_path']}")
    print(f"journal_dict_path={summary['journal_dict_path']}")


if __name__ == "__main__":
    main()
