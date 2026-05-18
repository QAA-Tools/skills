import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from openalex_client import fetch_openalex_works, inspect_openalex_work, normalize_title, parse_openalex_work


DEFAULT_OPENALEX_SORT = "relevance_score:desc"
LAST_30_DAYS = "last_30_days"


def pick_richer_value(left, right):
    if left:
        return left
    return right


def merge_stage1_records(left: dict, right: dict) -> dict:
    merged = dict(left)
    if not merged.get("abstract") and right.get("abstract"):
        merged["abstract"] = right["abstract"]
    if not merged.get("journal") and right.get("journal"):
        merged["journal"] = right["journal"]
    if not merged.get("doi") and right.get("doi"):
        merged["doi"] = right["doi"]
    if len(right.get("authors") or []) > len(merged.get("authors") or []):
        merged["authors"] = list(right.get("authors") or [])
    merged["first_author"] = (merged.get("authors") or [""])[0] if merged.get("authors") else ""

    raw_ids = list(merged.get("raw_source_ids") or [])
    for raw_id in right.get("raw_source_ids") or []:
        if raw_id not in raw_ids:
            raw_ids.append(raw_id)
    merged["raw_source_ids"] = raw_ids
    merged["raw_duplicate_count"] = int(merged.get("raw_duplicate_count") or 0) + int(right.get("raw_duplicate_count") or 0)

    if merged.get("doi"):
        merged["paper_url"] = f"https://doi.org/{merged['doi']}"
    elif not merged.get("paper_url") and right.get("paper_url"):
        merged["paper_url"] = right["paper_url"]

    if not merged.get("source_url") and right.get("source_url"):
        merged["source_url"] = right["source_url"]
    return merged


def dedup_stage1_records(records: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for record in records:
        key = record["title_normalized"]
        if key in deduped:
            deduped[key] = merge_stage1_records(deduped[key], record)
        else:
            deduped[key] = dict(record)
    return list(deduped.values())


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_stage1_config(path: str | Path) -> dict:
    config_path = Path(path)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    required = ["topic_query", "max_results", "outdir"]
    missing = [key for key in required if key not in config]
    if missing:
        raise ValueError(f"missing required config fields: {', '.join(missing)}")

    has_explicit_dates = "start_date" in config and "end_date" in config
    has_dynamic_window = bool(str(config.get("date_window") or "").strip())
    if not has_explicit_dates and not has_dynamic_window:
        raise ValueError("either start_date/end_date or date_window is required")
    if has_explicit_dates and has_dynamic_window:
        raise ValueError("use either start_date/end_date or date_window, not both")

    config["sort"] = str(config.get("sort") or DEFAULT_OPENALEX_SORT).strip()
    config = resolve_date_window(config)

    start = _parse_date(config["start_date"])
    end = _parse_date(config["end_date"])
    if start > end:
        raise ValueError("start_date must be <= end_date")
    if int(config["max_results"]) <= 0:
        raise ValueError("max_results must be > 0")

    outdir = Path(config["outdir"])
    outdir.mkdir(parents=True, exist_ok=True)
    config["max_results"] = int(config["max_results"])
    config["outdir"] = str(outdir)
    return config


def current_shanghai_date() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def current_shanghai_timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def resolve_date_window(config: dict, *, today: date | None = None) -> dict:
    resolved = dict(config)
    window = str(resolved.get("date_window") or "").strip()
    if not window:
        return resolved
    if window != LAST_30_DAYS:
        raise ValueError(f"unsupported date_window: {window}")
    current_day = today or current_shanghai_date()
    resolved["end_date"] = current_day.isoformat()
    resolved["start_date"] = (current_day - timedelta(days=30)).isoformat()
    return resolved


def build_timestamped_openalex_path(outdir: str | Path, retrieved_at: str) -> Path:
    outdir_path = Path(outdir)
    local_ts = retrieved_at.split("+")[0].replace(":", "-")
    return outdir_path / f"openalex_{local_ts}.jsonl"


def attach_raw_search_meta(raw_rows: list[dict], *, config: dict, retrieved_at: str, discard_reasons: dict[int, str] | None = None) -> list[dict]:
    sort = str(config.get("sort") or DEFAULT_OPENALEX_SORT).strip()
    search_meta = {
        "source": "openalex",
        "retrieved_at": retrieved_at,
        "topic_query": config["topic_query"],
        "start_date": config["start_date"],
        "end_date": config["end_date"],
        "max_results": int(config["max_results"]),
        "search_scope": {
            "endpoint": "works",
            "search_field": "search",
            "sort": sort,
            "publication_date_range": {
                "from": config["start_date"],
                "to": config["end_date"],
            },
        },
    }
    if config.get("date_window"):
        search_meta["search_scope"]["date_window"] = config["date_window"]
    annotated_rows = []
    discard_reasons = discard_reasons or {}
    for idx, row in enumerate(raw_rows):
        annotated = dict(row)
        annotated["search_meta"] = search_meta
        reason = discard_reasons.get(idx)
        if reason:
            annotated["discard_reason"] = reason
        annotated_rows.append(annotated)
    return annotated_rows


def collect_stage1_parse_results(raw_rows: list[dict], *, config: dict, retrieved_at: str) -> tuple[list[dict], dict[int, str]]:
    parsed_rows = []
    discard_reasons: dict[int, str] = {}
    title_first_seen: dict[str, int] = {}
    for idx, row in enumerate(raw_rows):
        parsed, discard_reason = inspect_openalex_work(
            row,
            topic_query=config["topic_query"],
            start_date=config["start_date"],
            end_date=config["end_date"],
            retrieved_at=retrieved_at,
        )
        if parsed is None:
            if discard_reason:
                discard_reasons[idx] = discard_reason
            continue
        title_key = parsed["title_normalized"]
        if title_key in title_first_seen:
            discard_reasons[idx] = "duplicate_title_normalized"
        else:
            title_first_seen[title_key] = idx
        parsed_rows.append(parsed)
    return parsed_rows, discard_reasons


def build_stage1_pool(config: dict) -> dict:
    outdir = Path(config["outdir"])
    retrieved_at = current_shanghai_timestamp()
    sort = str(config.get("sort") or DEFAULT_OPENALEX_SORT).strip()
    raw_rows = fetch_openalex_works(
        topic_query=config["topic_query"],
        start_date=config["start_date"],
        end_date=config["end_date"],
        max_results=config["max_results"],
        sort=sort,
    )
    parsed_rows, discard_reasons = collect_stage1_parse_results(raw_rows, config=config, retrieved_at=retrieved_at)
    raw_path = build_timestamped_openalex_path(outdir, retrieved_at)
    write_jsonl(raw_path, attach_raw_search_meta(raw_rows, config=config, retrieved_at=retrieved_at, discard_reasons=discard_reasons))

    pooled_rows = dedup_stage1_records(parsed_rows)
    for idx, row in enumerate(pooled_rows, start=1):
        row["record_id"] = f"oa_{idx:06d}"

    write_jsonl(outdir / "pool_stage1.jsonl", pooled_rows)
    return {"raw": len(raw_rows), "pool": len(pooled_rows), "outdir": str(outdir)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = load_stage1_config(args.config)
    summary = build_stage1_pool(config)
    print(f"raw={summary['raw']}")
    print(f"pool={summary['pool']}")
    print(f"outdir={summary['outdir']}")


if __name__ == "__main__":
    main()
