import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from journal_score_dict import (  # noqa: E402
    build_journal_score_cache,
    build_journal_dict_row,
    get_or_fetch_journal_score_payload,
    normalize_journal_name,
    round_score_value,
)
from journal_scorer import build_score_prompt, parse_numeric_score_response  # noqa: E402
from topic_stage2_score_and_enrich import (  # noqa: E402
    build_pool_record,
    extract_openalex_batch_ts,
    load_stage2_config,
    merge_pool_record,
    needs_abstract_enrichment,
    run_stage2,
    summarize_samples,
)


def sample_openalex_work(*, openalex_id: str, title: str, abstract_words=None, doi: str = "", journal: str = "Journal of Chemical Theory and Computation"):
    if abstract_words is None:
        abstract_words = ["This", "work", "uses", "TDDFT"]
    inverted_index = {word: [idx] for idx, word in enumerate(abstract_words)}
    return {
        "id": openalex_id,
        "type": "article",
        "publication_date": "2026-05-03",
        "display_name": title,
        "doi": doi,
        "abstract_inverted_index": inverted_index,
        "primary_location": {
            "landing_page_url": f"https://example.org/{openalex_id.split('/')[-1]}",
            "source": {"display_name": journal},
        },
        "authorships": [{"author": {"display_name": "Xi Zhang"}}],
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_standalone_journal_dict_module_builds_cache_and_rows():
    row = {
        "paper_journal": "Physical Review Letters",
        "score_journal_ai": 0.9533333333333334,
        "score_journal_ai_samples": [0.95],
        "score_journal_ai_model": "m1",
        "score_journal_ai_updated_at": "2026-05-04T10:00:00+08:00",
        "score_journal_impact": 8.9,
        "score_journal_impact_samples": [8.9],
        "score_journal_impact_model": "m2",
        "score_journal_impact_updated_at": "2026-05-04T10:00:00+08:00",
    }
    dict_row = build_journal_dict_row("Physical Review Letters", row, now_ts="2026-05-04T10:00:01+08:00")
    assert dict_row["journal_key"] == "physical review letters"
    assert dict_row["journal_name"] == "Physical Review Letters"
    assert dict_row["score_journal_ai"] == pytest.approx(0.95)
    assert normalize_journal_name("  Physical   Review Letters ") == "physical review letters"

    cache = build_journal_score_cache([dict_row], journal_field="journal_name")
    assert cache["physical review letters"]["score_journal_ai"] == pytest.approx(0.95)
    assert cache["physical review letters"]["score_journal_impact"] == pytest.approx(8.9)


def test_round_score_value_rounds_ai_style_scores_to_two_decimals():
    assert round_score_value(0.6966666666666667) == pytest.approx(0.7)
    assert round_score_value(0.016666666666666666) == pytest.approx(0.02)
    assert round_score_value(2.675) == pytest.approx(2.68)
    assert round_score_value(None) is None


def test_extract_openalex_batch_ts_from_filename():
    assert extract_openalex_batch_ts("/tmp/openalex_2026-05-03T16-00-00.jsonl") == "2026-05-03T16-00-00"
    with pytest.raises(ValueError, match="timestamp"):
        extract_openalex_batch_ts("/tmp/not_openalex.jsonl")


def test_get_or_fetch_journal_score_payload_reads_file_and_persists_on_miss(tmp_path):
    journal_dict_path = tmp_path / "journal_dict.jsonl"
    calls = []

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        calls.append((journal_name, score_kind, sample_index, model_name))
        return 0.91 if score_kind == "ai" else 6.2

    first_payload = get_or_fetch_journal_score_payload(
        "Shared Journal",
        journal_dict_path,
        now_ts="2026-05-05T13:30:00+08:00",
        score_sample_count=1,
        journal_ai_model="fake-ai-model",
        journal_impact_model="fake-impact-model",
        score_journal_fn=fake_score_journal,
    )

    assert first_payload["score_journal_ai"] == pytest.approx(0.91)
    assert first_payload["score_journal_impact"] == pytest.approx(6.2)
    assert len(calls) == 2
    saved_rows = [json.loads(line) for line in journal_dict_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(saved_rows) == 1
    assert saved_rows[0]["journal_name"] == "Shared Journal"

    second_payload = get_or_fetch_journal_score_payload(
        "Shared Journal",
        journal_dict_path,
        now_ts="2026-05-05T13:31:00+08:00",
        score_sample_count=1,
        journal_ai_model="fake-ai-model",
        journal_impact_model="fake-impact-model",
        score_journal_fn=fake_score_journal,
    )
    assert second_payload["score_journal_ai"] == pytest.approx(0.91)
    assert second_payload["score_journal_impact"] == pytest.approx(6.2)
    assert len(calls) == 2


def test_summarize_samples_uses_trimmed_mean_for_ai_values():
    result = summarize_samples([6.0, 8.0, 9.0, 10.0, 7.0], score_kind="ai")
    assert result == pytest.approx((7.0 + 8.0 + 9.0) / 3)

    assert summarize_samples([], score_kind="ai") is None
    assert summarize_samples([3.0], score_kind="ai") == 3.0



def test_summarize_samples_uses_mode_for_impact_values():
    result = summarize_samples([5.7, 5.5, 5.7, 5.7, 5.6], score_kind="impact")
    assert result == pytest.approx(5.7)



def test_summarize_samples_uses_closest_cluster_when_impact_has_no_mode():
    result = summarize_samples([1.5, 4.2, 4.3, 9.0, 12.0], score_kind="impact")
    assert result == pytest.approx((4.2 + 4.3) / 2)


def test_build_score_prompt_requires_number_only_not_json():
    ai_prompt = build_score_prompt("Physical Review Letters", "ai")
    impact_prompt = build_score_prompt("Nature", "impact")
    assert "只返回一个数字" in ai_prompt
    assert "只返回 JSON" not in ai_prompt
    assert "不要返回 JSON" in ai_prompt
    assert "小数点后 1 位" in ai_prompt
    assert "只返回一个数字" in impact_prompt
    assert "只返回 JSON" not in impact_prompt
    assert "不要返回 JSON" in impact_prompt
    assert "小数点后 1 位" in impact_prompt
    assert "0 到 1" in ai_prompt
    assert "PRL 按 0.95 参考" in ai_prompt
    assert "Science / Nature 正刊按 1.0 参考" in ai_prompt
    assert "与物理学不相干记为 0.0" in ai_prompt
    assert "Journal Impact Factor" in impact_prompt
    assert "非正式期刊、预印本平台、数据仓库返回 0" in impact_prompt
    assert "可对齐" not in impact_prompt
    assert "如果不确定" not in impact_prompt


def test_parse_numeric_score_response_requires_pure_number():
    assert parse_numeric_score_response(" 9.5 ") == pytest.approx(9.5)
    with pytest.raises(ValueError, match="pure number"):
        parse_numeric_score_response('{"score": 9.5}')
    with pytest.raises(ValueError, match="pure number"):
        parse_numeric_score_response("score=9.5")


def test_build_pool_record_uses_prefixed_fields_and_defaults():
    work = sample_openalex_work(openalex_id="https://openalex.org/W1", title="A TDDFT Study")
    record = build_pool_record(
        work,
        batch_ts="2026-05-03T16-00-00",
        retrieved_at="2026-05-03T16:00:30+08:00",
    )

    assert record["paper_id"].startswith("paper_")
    assert record["paper_title"] == "A TDDFT Study"
    assert record["paper_title_normalized"] == "a tddft study"
    assert record["source_openalex_batch_ts"] == ["2026-05-03T16-00-00"]
    assert record["paper_abstract_source"] == "openalex"
    assert "score_topic_query" not in record
    assert "score_topic_match" not in record
    assert record["paper_abstract_lookup_status"] == "not_needed"
    assert record["video_done"] is False
    assert record["content_brief"] == ""
    assert record["content_key_points"] == []


def test_build_pool_record_skips_rows_stage1_already_marked_discarded():
    work = sample_openalex_work(openalex_id="https://openalex.org/Wx", title="Discarded TDDFT Study")
    work["discard_reason"] = "duplicate_title_normalized"

    record = build_pool_record(
        work,
        batch_ts="2026-05-03T16-00-00",
        retrieved_at="2026-05-03T16:00:30+08:00",
    )

    assert record is None



def test_needs_abstract_enrichment_skips_already_missing_not_found_reason():
    row = {
        "paper_abstract": "",
        "paper_abstract_lookup_status": "not_found",
        "paper_abstract_lookup_reason": "Not found: no usable source",
    }
    assert needs_abstract_enrichment(row) is False

    row2 = {
        "paper_abstract": "",
        "paper_abstract_lookup_status": "",
        "paper_abstract_lookup_reason": "",
    }
    assert needs_abstract_enrichment(row2) is True


def test_merge_pool_record_dedups_and_backfills_missing_fields():
    base = build_pool_record(
        sample_openalex_work(openalex_id="https://openalex.org/W1", title="A TDDFT Study", abstract_words=[]),
        batch_ts="2026-05-03T16-00-00",
        retrieved_at="2026-05-03T16:00:30+08:00",
    )
    incoming = build_pool_record(
        sample_openalex_work(
            openalex_id="https://openalex.org/W2",
            title="A TDDFT Study",
            abstract_words=["Recovered", "abstract"],
            doi="10.1021/abc",
        ),
        batch_ts="2026-05-03T20-00-00",
        retrieved_at="2026-05-03T20:00:30+08:00",
    )

    merged = merge_pool_record(base, incoming)
    assert merged["paper_doi"] == "10.1021/abc"
    assert merged["paper_abstract"] == "Recovered abstract"
    assert merged["source_openalex_batch_ts"] == ["2026-05-03T16-00-00", "2026-05-03T20-00-00"]
    assert merged["pool_duplicate_count"] == 2
    assert "score_topic_query" not in merged
    assert "score_topic_match" not in merged


def test_load_stage2_config_validates_inputs(tmp_path):
    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    openalex_file.write_text("{}\n", encoding="utf-8")
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "openalex_inputs": [str(openalex_file)],
                "pool_path": str(tmp_path / "pool.jsonl"),
                "journal_ai_threshold": 0.8,
                "journal_impact_threshold": 5.0,
            }
        ),
        encoding="utf-8",
    )
    config = load_stage2_config(config_path)
    assert config["score_sample_count"] == 5
    assert config["score_batch_limit"] is None
    assert config["run_log_path"].endswith("pool.run_log.jsonl")
    assert config["journal_dict_path"].endswith("pool.journal_dict.jsonl")
    assert config["journal_ai_threshold"] == 0.8
    assert config["journal_impact_threshold"] == 5.0


def test_run_stage2_merges_multiple_openalex_batches_and_scores_pool(tmp_path):
    first_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    second_file = tmp_path / "openalex_2026-05-03T20-00-00.jsonl"
    write_jsonl(
        first_file,
        [
            sample_openalex_work(openalex_id="https://openalex.org/W1", title="A TDDFT Study", abstract_words=[]),
            sample_openalex_work(openalex_id="https://openalex.org/W2", title="Another Exciton TDDFT Paper", journal="Nature"),
        ],
    )
    write_jsonl(
        second_file,
        [
            sample_openalex_work(
                openalex_id="https://openalex.org/W3",
                title="A TDDFT Study",
                abstract_words=["Recovered", "abstract"],
                doi="10.1021/abc",
            )
        ],
    )

    config = {
        "openalex_inputs": [str(first_file), str(second_file)],
        "pool_path": str(tmp_path / "topic_paper_pool.jsonl"),
        "journal_ai_threshold": 0.8,
        "journal_impact_threshold": 5.0,
        "score_sample_count": 5,
        "journal_ai_model": "fake-ai-model",
        "journal_impact_model": "fake-impact-model",
    }

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        if score_kind == "ai":
            mapping = {
                "journal of chemical theory and computation": [0.60, 0.65, 0.62, 0.63, 0.66],
                "nature": [0.99, 0.98, 1.00, 0.99, 0.98],
            }
        else:
            mapping = {
                "journal of chemical theory and computation": [4.8, 5.0, 5.2, 5.1, 4.9],
                "nature": [50.0, 49.5, 50.5, 49.8, 50.2],
            }
        return mapping[journal_name.lower()][sample_index]

    summary = run_stage2(config, score_journal_fn=fake_score_journal)

    pool_path = Path(config["pool_path"])
    assert pool_path.exists()
    pool_rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(pool_rows) == 2
    assert summary["ingested_openalex_rows"] == 3
    assert summary["pool_row_count"] == 2

    first = next(row for row in pool_rows if row["paper_title"] == "A TDDFT Study")
    assert first["paper_doi"] == "10.1021/abc"
    assert first["score_journal_ai"] == pytest.approx(0.63)
    assert first["score_journal_impact"] == pytest.approx(4.95)
    assert first["score_journal_ai_model"] == "fake-ai-model"
    assert first["score_journal_impact_model"] == "fake-impact-model"
    assert first["score_candidate_passed"] is False
    assert "score_topic_query" not in first
    assert "score_topic_match" not in first

    second = next(row for row in pool_rows if row["paper_title"] == "Another Exciton TDDFT Paper")
    assert second["score_journal_ai"] > 0.98
    assert second["score_journal_impact"] > 49.0
    assert second["source_openalex_batch_ts"] == ["2026-05-03T16-00-00"]


def test_run_stage2_is_idempotent_for_same_openalex_input(tmp_path):
    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    write_jsonl(
        openalex_file,
        [sample_openalex_work(openalex_id="https://openalex.org/W1", title="Stable Paper")],
    )
    config = {
        "openalex_inputs": [str(openalex_file)],
        "pool_path": str(tmp_path / "topic_paper_pool.jsonl"),
        "journal_ai_threshold": 0.8,
        "journal_impact_threshold": 5.0,
        "score_sample_count": 5,
        "journal_ai_model": "fake-ai-model",
        "journal_impact_model": "fake-impact-model",
    }

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        return 0.9 if score_kind == "ai" else 6.0

    run_stage2(config, score_journal_fn=fake_score_journal)
    run_stage2(config, score_journal_fn=fake_score_journal)

    rows = [json.loads(line) for line in Path(config["pool_path"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["pool_duplicate_count"] == 1
    assert rows[0]["source_openalex_batch_ts"] == ["2026-05-03T16-00-00"]


def test_run_stage2_persists_journal_dictionary_separately_and_reuses_it_after_pool_reset(tmp_path):
    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    write_jsonl(
        openalex_file,
        [sample_openalex_work(openalex_id="https://openalex.org/W1", title="Stable Paper", journal="Shared Journal")],
    )
    pool_path = tmp_path / "topic_paper_pool.jsonl"
    journal_dict_path = tmp_path / "journal_dict.jsonl"
    config = {
        "openalex_inputs": [str(openalex_file)],
        "pool_path": str(pool_path),
        "journal_dict_path": str(journal_dict_path),
        "journal_ai_threshold": 0.8,
        "journal_impact_threshold": 5.0,
        "score_sample_count": 1,
        "journal_ai_model": "fake-ai-model",
        "journal_impact_model": "fake-impact-model",
    }
    score_calls = []

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        score_calls.append((journal_name, score_kind, sample_index))
        return 0.91 if score_kind == "ai" else 6.2

    first_summary = run_stage2(config, score_journal_fn=fake_score_journal)
    assert first_summary["scored_this_run"] == 1
    assert len(score_calls) == 2
    assert journal_dict_path.exists()

    journal_rows = [json.loads(line) for line in journal_dict_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(journal_rows) == 1
    assert journal_rows[0]["journal_name"] == "Shared Journal"
    assert journal_rows[0]["journal_key"] == "shared journal"
    assert journal_rows[0]["score_journal_ai"] == pytest.approx(0.91)
    assert journal_rows[0]["score_journal_impact"] == pytest.approx(6.2)

    pool_path.unlink()
    second_summary = run_stage2(config, score_journal_fn=fake_score_journal)
    assert second_summary["scored_this_run"] == 1
    assert len(score_calls) == 2

    rebuilt_rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rebuilt_rows) == 1
    assert rebuilt_rows[0]["score_journal_ai"] == pytest.approx(0.91)
    assert rebuilt_rows[0]["score_journal_impact"] == pytest.approx(6.2)


def test_run_stage2_counts_new_batch_hit_even_with_same_openalex_id(tmp_path):
    first_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    second_file = tmp_path / "openalex_2026-05-03T20-00-00.jsonl"
    record = sample_openalex_work(openalex_id="https://openalex.org/W1", title="Stable Paper")
    write_jsonl(first_file, [record])
    write_jsonl(second_file, [record])
    pool_path = tmp_path / "topic_paper_pool.jsonl"

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        return 0.9 if score_kind == "ai" else 6.0

    run_stage2({
        "openalex_inputs": [str(first_file)],
        "pool_path": str(pool_path),
        "journal_ai_threshold": 0.8,
        "journal_impact_threshold": 5.0,
        "score_sample_count": 5,
        "journal_ai_model": "fake-ai-model",
        "journal_impact_model": "fake-impact-model",
    }, score_journal_fn=fake_score_journal)
    run_stage2({
        "openalex_inputs": [str(second_file)],
        "pool_path": str(pool_path),
        "journal_ai_threshold": 0.8,
        "journal_impact_threshold": 5.0,
        "score_sample_count": 5,
        "journal_ai_model": "fake-ai-model",
        "journal_impact_model": "fake-impact-model",
    }, score_journal_fn=fake_score_journal)

    rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["pool_duplicate_count"] == 2
    assert rows[0]["source_openalex_batch_ts"] == ["2026-05-03T16-00-00", "2026-05-03T20-00-00"]
    assert "source_topic_queries" not in rows[0]


def test_run_stage2_does_not_keep_or_generate_topic_score_fields(tmp_path):
    pool_path = tmp_path / "topic_paper_pool.jsonl"
    existing = build_pool_record(
        sample_openalex_work(openalex_id="https://openalex.org/W9", title="Old Topic Paper"),
        batch_ts="2026-05-01T10-00-00",
        retrieved_at="2026-05-01T10:00:00+08:00",
    )
    existing["score_topic_query"] = "OLDTOPIC"
    existing["score_topic_match"] = 0.7
    write_jsonl(pool_path, [existing])

    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    write_jsonl(
        openalex_file,
        [sample_openalex_work(openalex_id="https://openalex.org/W1", title="New TDDFT Paper")],
    )
    config = {
        "openalex_inputs": [str(openalex_file)],
        "pool_path": str(pool_path),
        "journal_ai_threshold": 0.8,
        "journal_impact_threshold": 5.0,
        "score_sample_count": 5,
        "journal_ai_model": "fake-ai-model",
        "journal_impact_model": "fake-impact-model",
    }

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        return 0.9 if score_kind == "ai" else 6.0

    run_stage2(config, score_journal_fn=fake_score_journal)

    rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    old_row = next(row for row in rows if row["paper_title"] == "Old Topic Paper")
    new_row = next(row for row in rows if row["paper_title"] == "New TDDFT Paper")
    assert "score_topic_query" not in new_row
    assert "score_topic_match" not in new_row
    assert old_row.get("score_topic_query") == "OLDTOPIC"
    assert old_row.get("score_topic_match") == 0.7


def test_run_stage2_processes_only_batch_limit_and_resume_skips_completed_rows_with_journal_reuse(tmp_path):
    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    rows = [
        sample_openalex_work(
            openalex_id=f"https://openalex.org/W{i}",
            title=f"TDDFT Paper {i}",
            abstract_words=[] if i in (2, 4, 6) else None,
            journal="Journal A" if i <= 4 else "Journal B",
        )
        for i in range(1, 9)
    ]
    write_jsonl(openalex_file, rows)
    pool_path = tmp_path / "topic_paper_pool.jsonl"
    run_log_path = tmp_path / "stage2_runs.jsonl"
    progress_log_path = tmp_path / "stage2_progress.jsonl"
    score_calls = []
    abstract_calls = []

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        score_calls.append((journal_name, score_kind, sample_index))
        return 0.9 if score_kind == "ai" else 6.0

    def fake_enrich_abstract(row: dict, config: dict) -> dict | None:
        abstract_calls.append(row["paper_title"])
        if row["paper_title"] == "TDDFT Paper 2":
            return {"abstract": "Recovered abstract 2", "source": "arxiv"}
        if row["paper_title"] == "TDDFT Paper 4":
            return None
        if row["paper_title"] == "TDDFT Paper 6":
            return {"abstract": "Recovered abstract 6", "source": "doi"}
        return None

    config = {
        "openalex_inputs": [str(openalex_file)],
        "pool_path": str(pool_path),
        "run_log_path": str(run_log_path),
        "progress_log_path": str(progress_log_path),
        "journal_ai_threshold": 0.8,
        "journal_impact_threshold": 5.0,
        "score_sample_count": 1,
        "score_batch_limit": 5,
        "enable_abstract_enrichment": True,
        "journal_ai_model": "fake-ai-model",
        "journal_impact_model": "fake-impact-model",
    }
    first_summary = run_stage2(config, score_journal_fn=fake_score_journal, enrich_abstract_fn=fake_enrich_abstract)
    first_rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    processed_after_first = [
        row for row in first_rows
        if row["score_journal_ai"] is not None and row["score_journal_impact"] is not None and (row["paper_abstract"] or row["paper_abstract_lookup_status"] == "not_found")
    ]
    pending_after_first = [row for row in first_rows if needs_abstract_enrichment(row) or row["score_journal_ai"] is None or row["score_journal_impact"] is None]
    assert first_summary["status"] == "OK_PROGRESS"
    assert first_summary["processed_this_run"] == 5
    assert first_summary["scored_this_run"] == 5
    assert first_summary["remaining_to_process"] == 3
    assert len(processed_after_first) == 5
    assert len(pending_after_first) == 3
    assert len(score_calls) == 4
    assert abstract_calls == ["TDDFT Paper 2", "TDDFT Paper 4"]

    second_summary = run_stage2(config, score_journal_fn=fake_score_journal, enrich_abstract_fn=fake_enrich_abstract)
    second_rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    fully_processed_after_second = [
        row for row in second_rows
        if row["score_journal_ai"] is not None and row["score_journal_impact"] is not None and (row["paper_abstract"] or row["paper_abstract_lookup_status"] == "not_found")
    ]
    assert second_summary["status"] == "OK_NO_REMAINING"
    assert second_summary["processed_this_run"] == 3
    assert second_summary["scored_this_run"] == 3
    assert second_summary["remaining_to_process"] == 0
    assert len(fully_processed_after_second) == 8
    assert len(score_calls) == 4
    assert abstract_calls == ["TDDFT Paper 2", "TDDFT Paper 4", "TDDFT Paper 6"]

    paper4 = next(row for row in second_rows if row["paper_title"] == "TDDFT Paper 4")
    assert paper4["paper_abstract_lookup_status"] == "not_found"
    assert "Not found" in paper4["paper_abstract_lookup_reason"]

    third_summary = run_stage2(config, score_journal_fn=fake_score_journal, enrich_abstract_fn=fake_enrich_abstract)
    assert third_summary["status"] == "OK_NO_REMAINING"
    assert third_summary["processed_this_run"] == 0
    assert third_summary["scored_this_run"] == 0
    assert third_summary["remaining_to_process"] == 0
    assert len(score_calls) == 4
    assert abstract_calls == ["TDDFT Paper 2", "TDDFT Paper 4", "TDDFT Paper 6"]

    run_logs = [json.loads(line) for line in run_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [item["status"] for item in run_logs] == ["OK_PROGRESS", "OK_NO_REMAINING", "OK_NO_REMAINING"]
    assert run_logs[0]["processed_this_run"] == 5
    assert run_logs[1]["remaining_to_process"] == 0
    assert run_logs[2]["processed_this_run"] == 0

    progress_logs = [json.loads(line) for line in progress_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert progress_logs[0]["event"] == "run_started"
    assert progress_logs[0]["source"] == "stage2"
    assert any(item["event"] == "row_started" for item in progress_logs)
    assert any(item["event"] == "row_finished" for item in progress_logs)
    assert progress_logs[-1]["event"] == "run_completed"


def test_run_stage2_persists_each_processed_row_before_next_row_starts(tmp_path):
    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    write_jsonl(
        openalex_file,
        [
            sample_openalex_work(openalex_id="https://openalex.org/W1", title="TDDFT Paper A", journal="Journal A"),
            sample_openalex_work(openalex_id="https://openalex.org/W2", title="TDDFT Paper B", journal="Journal B"),
        ],
    )
    pool_path = tmp_path / "topic_paper_pool.jsonl"

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        if journal_name == "Journal B" and score_kind == "ai":
            rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            row_a = next(row for row in rows if row["paper_title"] == "TDDFT Paper A")
            assert row_a["score_journal_ai"] == 0.9
            assert row_a["score_journal_impact"] == 6.0
        return 0.9 if score_kind == "ai" else 6.0

    summary = run_stage2(
        {
            "openalex_inputs": [str(openalex_file)],
            "pool_path": str(pool_path),
            "journal_ai_threshold": 0.8,
            "journal_impact_threshold": 5.0,
            "score_sample_count": 1,
            "journal_ai_model": "fake-ai-model",
            "journal_impact_model": "fake-impact-model",
        },
        score_journal_fn=fake_score_journal,
    )

    rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert summary["status"] == "OK_NO_REMAINING"
    assert len(rows) == 2
    assert all(row["score_journal_ai"] == 0.9 for row in rows)
    assert all(row["score_journal_impact"] == 6.0 for row in rows)



def test_run_stage2_tolerates_single_record_scoring_failure_and_keeps_progress(tmp_path):
    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    write_jsonl(
        openalex_file,
        [
            sample_openalex_work(openalex_id="https://openalex.org/W1", title="TDDFT Paper A", journal="Fail Journal"),
            sample_openalex_work(openalex_id="https://openalex.org/W2", title="TDDFT Paper B", journal="Good Journal"),
        ],
    )
    pool_path = tmp_path / "topic_paper_pool.jsonl"
    run_log_path = tmp_path / "stage2_runs.jsonl"

    def fake_score_journal(journal_name: str, score_kind: str, sample_index: int, model_name: str) -> float:
        if journal_name == "Fail Journal":
            raise RuntimeError("score request failed")
        return 0.9 if score_kind == "ai" else 6.0

    summary = run_stage2(
        {
            "openalex_inputs": [str(openalex_file)],
            "pool_path": str(pool_path),
            "run_log_path": str(run_log_path),
            "journal_ai_threshold": 0.8,
            "journal_impact_threshold": 5.0,
            "score_sample_count": 1,
            "score_batch_limit": 5,
            "journal_ai_model": "fake-ai-model",
            "journal_impact_model": "fake-impact-model",
        },
        score_journal_fn=fake_score_journal,
    )

    rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    failed_row = next(row for row in rows if row["paper_title"] == "TDDFT Paper A")
    good_row = next(row for row in rows if row["paper_title"] == "TDDFT Paper B")
    assert summary["status"] == "OK_PROGRESS"
    assert summary["scored_this_run"] == 1
    assert summary["remaining_to_score"] == 1
    assert failed_row["score_journal_ai"] is None
    assert good_row["score_journal_ai"] == 0.9

    run_logs = [json.loads(line) for line in run_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert run_logs[-1]["status"] == "OK_PROGRESS"
    assert run_logs[-1]["remaining_to_score"] == 1
