import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re


def normalize_journal_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def round_score_value(value: float | int | None, *, ndigits: int = 2) -> float | None:
    if value is None:
        return None
    quantizer = Decimal("1").scaleb(-ndigits)
    return float(Decimal(str(value)).quantize(quantizer, rounding=ROUND_HALF_UP))


def extract_journal_score_payload(row: dict) -> dict | None:
    if row.get("score_journal_ai") is None or row.get("score_journal_impact") is None:
        return None
    return {
        "score_journal_ai": round_score_value(row.get("score_journal_ai")),
        "score_journal_ai_samples": list(row.get("score_journal_ai_samples") or []),
        "score_journal_ai_model": row.get("score_journal_ai_model") or "",
        "score_journal_ai_updated_at": row.get("score_journal_ai_updated_at") or "",
        "score_journal_impact": round_score_value(row.get("score_journal_impact")),
        "score_journal_impact_samples": list(row.get("score_journal_impact_samples") or []),
        "score_journal_impact_model": row.get("score_journal_impact_model") or "",
        "score_journal_impact_updated_at": row.get("score_journal_impact_updated_at") or "",
    }


def build_journal_dict_row(journal_name: str, row: dict, *, now_ts: str) -> dict:
    payload = extract_journal_score_payload(row) or {}
    return {
        "journal_key": normalize_journal_name(journal_name),
        "journal_name": (journal_name or "").strip(),
        **payload,
        "audit_updated_at": now_ts,
        "audit_last_stage": "stage2_journal_dictionary",
    }


def build_journal_score_cache(rows: list[dict], *, journal_field: str = "paper_journal") -> dict[str, dict]:
    cache = {}
    for row in rows:
        journal_name = row.get(journal_field) or row.get("journal_name") or ""
        journal_key = row.get("journal_key") or normalize_journal_name(journal_name)
        if not journal_key:
            continue
        payload = extract_journal_score_payload(row)
        if payload is None:
            continue
        cache[journal_key] = payload
    return cache


def read_journal_dict(path: str | Path) -> list[dict]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    rows = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_journal_dict(path: str | Path, rows: list[dict]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def summarize_journal_samples(samples: list[float], *, score_kind: str) -> float | None:
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


def get_or_fetch_journal_score_payload(
    journal_name: str,
    path: str | Path,
    *,
    now_ts: str,
    score_sample_count: int,
    journal_ai_model: str,
    journal_impact_model: str,
    score_journal_fn,
    force_rescore: bool = False,
) -> dict | None:
    journal_key = normalize_journal_name(journal_name)
    if not journal_key:
        return None

    existing_rows = read_journal_dict(path)
    existing_by_key = {
        row.get("journal_key") or normalize_journal_name(row.get("journal_name") or ""): dict(row)
        for row in existing_rows
        if (row.get("journal_key") or normalize_journal_name(row.get("journal_name") or ""))
    }
    if not force_rescore:
        cached = build_journal_score_cache(existing_rows, journal_field="journal_name").get(journal_key)
        if cached is not None:
            return cached

    samples_ai = [float(score_journal_fn(journal_name, "ai", idx, journal_ai_model)) for idx in range(score_sample_count)]
    samples_impact = [float(score_journal_fn(journal_name, "impact", idx, journal_impact_model)) for idx in range(score_sample_count)]
    payload = {
        "score_journal_ai": round_score_value(summarize_journal_samples(samples_ai, score_kind="ai")),
        "score_journal_ai_samples": samples_ai,
        "score_journal_ai_model": journal_ai_model,
        "score_journal_ai_updated_at": now_ts,
        "score_journal_impact": round_score_value(summarize_journal_samples(samples_impact, score_kind="impact")),
        "score_journal_impact_samples": samples_impact,
        "score_journal_impact_model": journal_impact_model,
        "score_journal_impact_updated_at": now_ts,
    }
    dict_row = build_journal_dict_row(journal_name, payload, now_ts=now_ts)
    existing_by_key[journal_key] = dict_row
    write_journal_dict(path, [existing_by_key[key] for key in sorted(existing_by_key.keys())])
    return extract_journal_score_payload(dict_row)
