import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from topic_stage1_openalex_pool import (  # noqa: E402
    LAST_30_DAYS,
    attach_raw_search_meta,
    build_stage1_pool,
    build_timestamped_openalex_path,
    dedup_stage1_records,
    fetch_openalex_works,
    load_stage1_config,
    merge_stage1_records,
    normalize_title,
    parse_openalex_work,
    resolve_date_window,
)


def test_normalize_title_collapses_basic_punctuation_and_space():
    a = "Magnetic-Field-Driven Insulator:Superconductor Transition"
    b = "  magnetic field driven   insulator superconductor transition  "
    assert normalize_title(a) == normalize_title(b)


def test_normalize_title_strips_html_and_underscores():
    text = "A_Unified Formulation for ⟨ <i>Ŝ</i> <sup>2</sup> ⟩ in Two-Component TDDFT"
    assert normalize_title(text) == "a unified formulation for ⟨ ŝ 2 ⟩ in two component tddft"


def test_build_timestamped_openalex_path_uses_flat_timestamped_filename(tmp_path):
    path = build_timestamped_openalex_path(tmp_path, "2026-05-03T16:00:30+08:00")
    assert path == tmp_path / "openalex_2026-05-03T16-00-30.jsonl"


def test_attach_raw_search_meta_embeds_query_time_and_scope():
    raw_rows = [{"id": "W1", "display_name": "Example"}]
    config = {
        "topic_query": "TDDFT",
        "start_date": "2026-04-26",
        "end_date": "2026-05-03",
        "max_results": 100,
        "outdir": "/tmp/ignored",
        "sort": "relevance_score:desc",
    }

    annotated = attach_raw_search_meta(
        raw_rows,
        config=config,
        retrieved_at="2026-05-03T12:00:00+08:00",
    )

    assert annotated[0]["id"] == "W1"
    assert annotated[0]["search_meta"]["retrieved_at"] == "2026-05-03T12:00:00+08:00"
    assert annotated[0]["search_meta"]["topic_query"] == "TDDFT"
    assert annotated[0]["search_meta"]["search_scope"]["publication_date_range"] == {
        "from": "2026-04-26",
        "to": "2026-05-03",
    }
    assert annotated[0]["search_meta"]["search_scope"]["sort"] == "relevance_score:desc"


def test_merge_stage1_records_prefers_non_empty_fields_and_longer_authors():
    left = {
        "source": "openalex",
        "openalex_id": "W1",
        "topic_query": "TDDFT",
        "start_date": "2026-04-26",
        "end_date": "2026-05-03",
        "retrieved_at": "2026-05-03T12:00:00+08:00",
        "title": "Example Title",
        "title_normalized": "example title",
        "abstract": "",
        "publication_date": "2026-05-01",
        "journal": "",
        "doi": "",
        "paper_url": "",
        "source_url": "https://source/1",
        "authors": [],
        "first_author": "",
        "raw_source_ids": ["W1"],
        "raw_duplicate_count": 1,
    }
    right = {
        "source": "openalex",
        "openalex_id": "W2",
        "topic_query": "TDDFT",
        "start_date": "2026-04-26",
        "end_date": "2026-05-03",
        "retrieved_at": "2026-05-03T12:00:00+08:00",
        "title": "Example Title",
        "title_normalized": "example title",
        "abstract": "Useful abstract",
        "publication_date": "2026-04-30",
        "journal": "Journal X",
        "doi": "10.1/test",
        "paper_url": "https://fallback.example/paper",
        "source_url": "https://source/2",
        "authors": ["A Author", "B Author"],
        "first_author": "A Author",
        "raw_source_ids": ["W2"],
        "raw_duplicate_count": 1,
    }

    merged = merge_stage1_records(left, right)

    assert merged["abstract"] == "Useful abstract"
    assert merged["journal"] == "Journal X"
    assert merged["doi"] == "10.1/test"
    assert merged["paper_url"] == "https://doi.org/10.1/test"
    assert merged["source_url"] == "https://source/1"
    assert merged["authors"] == ["A Author", "B Author"]
    assert merged["first_author"] == "A Author"
    assert merged["publication_date"] == "2026-05-01"
    assert merged["raw_duplicate_count"] == 2
    assert merged["raw_source_ids"] == ["W1", "W2"]


def test_dedup_stage1_records_merges_exact_normalized_title_matches():
    records = [
        {
            "title": "A-B",
            "title_normalized": "a b",
            "openalex_id": "W1",
            "raw_source_ids": ["W1"],
            "raw_duplicate_count": 1,
            "source": "openalex",
            "topic_query": "TDDFT",
            "start_date": "2026-04-26",
            "end_date": "2026-05-03",
            "retrieved_at": "2026-05-03T12:00:00+08:00",
            "abstract": "",
            "publication_date": "2026-05-01",
            "journal": "",
            "doi": "",
            "paper_url": "",
            "source_url": "",
            "authors": [],
            "first_author": "",
        },
        {
            "title": "A B",
            "title_normalized": "a b",
            "openalex_id": "W2",
            "raw_source_ids": ["W2"],
            "raw_duplicate_count": 1,
            "source": "openalex",
            "topic_query": "TDDFT",
            "start_date": "2026-04-26",
            "end_date": "2026-05-03",
            "retrieved_at": "2026-05-03T12:00:00+08:00",
            "abstract": "Useful abstract",
            "publication_date": "2026-04-30",
            "journal": "JCTC",
            "doi": "",
            "paper_url": "",
            "source_url": "",
            "authors": [],
            "first_author": "",
        },
    ]

    out = dedup_stage1_records(records)
    assert len(out) == 1
    assert out[0]["raw_duplicate_count"] == 2
    assert out[0]["raw_source_ids"] == ["W1", "W2"]
    assert out[0]["abstract"] == "Useful abstract"


def sample_work():
    return {
        "id": "https://openalex.org/W123",
        "display_name": "Optical excitations in nanographenes from the BSE and TDDFT",
        "publication_date": "2026-04-27",
        "doi": "https://doi.org/10.5281/zenodo.19917998",
        "type": "article",
        "primary_location": {
            "landing_page_url": "https://zenodo.org/records/19917998",
            "source": {"display_name": "Zenodo"},
        },
        "authorships": [
            {"author": {"display_name": "Maximilian Graml"}},
            {"author": {"display_name": "Jan Wilhelm"}},
        ],
        "abstract_inverted_index": {
            "Data": [0],
            "from": [1],
            "excited": [2],
            "state": [3],
            "calculations": [4],
            "employing": [5],
            "GW-BSE": [6],
            "and": [7],
            "TDDFT.": [8],
        },
    }


def test_parse_openalex_work_extracts_expected_fields():
    parsed = parse_openalex_work(
        sample_work(),
        topic_query="TDDFT",
        start_date="2026-04-26",
        end_date="2026-05-03",
        retrieved_at="2026-05-03T12:00:00+08:00",
    )

    assert parsed["openalex_id"] == "https://openalex.org/W123"
    assert parsed["title"] == "Optical excitations in nanographenes from the BSE and TDDFT"
    assert parsed["title_normalized"] == "optical excitations in nanographenes from the bse and tddft"
    assert parsed["abstract"] == "Data from excited state calculations employing GW-BSE and TDDFT."
    assert parsed["publication_date"] == "2026-04-27"
    assert parsed["journal"] == "Zenodo"
    assert parsed["doi"] == "10.5281/zenodo.19917998"
    assert parsed["paper_url"] == "https://doi.org/10.5281/zenodo.19917998"
    assert parsed["source_url"] == "https://zenodo.org/records/19917998"
    assert parsed["authors"] == ["Maximilian Graml", "Jan Wilhelm"]
    assert parsed["first_author"] == "Maximilian Graml"
    assert parsed["raw_source_ids"] == ["https://openalex.org/W123"]
    assert parsed["raw_duplicate_count"] == 1


def test_parse_openalex_work_returns_none_outside_requested_window():
    work = sample_work()
    work["publication_date"] = "2026-04-20"

    assert (
        parse_openalex_work(
            work,
            topic_query="TDDFT",
            start_date="2026-04-26",
            end_date="2026-05-03",
            retrieved_at="2026-05-03T12:00:00+08:00",
        )
        is None
    )


def test_parse_openalex_work_returns_none_for_empty_normalized_title():
    work = sample_work()
    work["display_name"] = " - / : "

    assert (
        parse_openalex_work(
            work,
            topic_query="TDDFT",
            start_date="2026-04-26",
            end_date="2026-05-03",
            retrieved_at="2026-05-03T12:00:00+08:00",
        )
        is None
    )


def test_parse_openalex_work_returns_none_for_non_whitelisted_type():
    work = sample_work()
    work["type"] = "book"

    assert (
        parse_openalex_work(
            work,
            topic_query="TDDFT",
            start_date="2026-04-26",
            end_date="2026-05-03",
            retrieved_at="2026-05-03T12:00:00+08:00",
        )
        is None
    )


def test_fetch_openalex_works_respects_max_results_and_pagination(monkeypatch):
    payloads = [
        {"results": [{"id": "W1"}, {"id": "W2"}]},
        {"results": [{"id": "W3"}, {"id": "W4"}]},
    ]
    calls = []

    class DummyResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, *, params, headers, timeout):
        calls.append({"url": url, "params": params, "headers": headers, "timeout": timeout})
        return DummyResponse(payloads[len(calls) - 1])

    monkeypatch.setattr("openalex_client.requests.get", fake_get)

    rows = fetch_openalex_works(
        topic_query="TDDFT",
        start_date="2026-04-26",
        end_date="2026-05-03",
        max_results=3,
        sort="relevance_score:desc",
    )

    assert [row["id"] for row in rows] == ["W1", "W2", "W3"]
    assert calls[0]["params"]["search"] == "TDDFT"
    assert calls[0]["params"]["sort"] == "relevance_score:desc"
    assert "HermesAgent" in calls[0]["headers"]["User-Agent"]
    assert calls[0]["timeout"] == 60


def test_load_stage1_config_validates_required_fields_and_range(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "topic_query": "TDDFT",
                "start_date": "2026-04-26",
                "end_date": "2026-05-03",
                "max_results": 20,
                "outdir": str(tmp_path / "out"),
            }
        ),
        encoding="utf-8",
    )

    config = load_stage1_config(config_path)
    assert config["topic_query"] == "TDDFT"
    assert config["max_results"] == 20
    assert config["sort"] == "relevance_score:desc"
    assert Path(config["outdir"]).exists()


def test_resolve_date_window_last_30_days_uses_realtime_shanghai_day():
    resolved = resolve_date_window({"date_window": LAST_30_DAYS}, today=date(2026, 5, 5))
    assert resolved["start_date"] == "2026-04-05"
    assert resolved["end_date"] == "2026-05-05"


def test_load_stage1_config_expands_last_30_days_window(tmp_path, monkeypatch):
    config_path = tmp_path / "dynamic.json"
    config_path.write_text(
        json.dumps(
            {
                "topic_query": "TDDFT",
                "date_window": LAST_30_DAYS,
                "max_results": 20,
                "outdir": str(tmp_path / "out"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("topic_stage1_openalex_pool.current_shanghai_date", lambda: date(2026, 5, 5))

    config = load_stage1_config(config_path)
    assert config["date_window"] == LAST_30_DAYS
    assert config["start_date"] == "2026-04-05"
    assert config["end_date"] == "2026-05-05"
    assert config["sort"] == "relevance_score:desc"


def test_load_stage1_config_rejects_invalid_dates_or_max_results(tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(
        json.dumps(
            {
                "topic_query": "TDDFT",
                "start_date": "2026-05-03",
                "end_date": "2026-04-26",
                "max_results": 0,
                "outdir": str(tmp_path / "out"),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_stage1_config(bad_path)


def test_load_stage1_config_rejects_missing_or_conflicting_date_inputs(tmp_path):
    missing_date_path = tmp_path / "missing_date.json"
    missing_date_path.write_text(
        json.dumps(
            {
                "topic_query": "TDDFT",
                "max_results": 20,
                "outdir": str(tmp_path / "out1"),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_stage1_config(missing_date_path)

    conflicting_path = tmp_path / "conflicting.json"
    conflicting_path.write_text(
        json.dumps(
            {
                "topic_query": "TDDFT",
                "start_date": "2026-04-26",
                "end_date": "2026-05-03",
                "date_window": LAST_30_DAYS,
                "max_results": 20,
                "outdir": str(tmp_path / "out2"),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_stage1_config(conflicting_path)


def test_build_stage1_pool_writes_expected_files(tmp_path, monkeypatch):
    raw_rows = [sample_work(), sample_work() | {"id": "https://openalex.org/W124"}]

    monkeypatch.setattr(
        "topic_stage1_openalex_pool.fetch_openalex_works",
        lambda **kwargs: raw_rows,
    )
    monkeypatch.setattr(
        "topic_stage1_openalex_pool.current_shanghai_timestamp",
        lambda: "2026-05-03T12:00:00+08:00",
    )

    config = {
        "topic_query": "TDDFT",
        "start_date": "2026-04-26",
        "end_date": "2026-05-03",
        "max_results": 20,
        "outdir": str(tmp_path / "out"),
        "sort": "relevance_score:desc",
    }

    summary = build_stage1_pool(config)

    raw_path = Path(config["outdir"]) / "openalex_2026-05-03T12-00-00.jsonl"
    pool_path = Path(config["outdir"]) / "pool_stage1.jsonl"

    assert raw_path.exists()
    assert pool_path.exists()
    assert summary["raw"] == 2
    assert summary["pool"] == 1

    raw_lines = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(raw_lines) == 2
    assert raw_lines[0]["search_meta"]["topic_query"] == "TDDFT"
    assert raw_lines[0]["search_meta"]["retrieved_at"] == "2026-05-03T12:00:00+08:00"
    assert raw_lines[0]["search_meta"]["search_scope"]["publication_date_range"] == {
        "from": "2026-04-26",
        "to": "2026-05-03",
    }
    assert raw_lines[0]["search_meta"]["search_scope"]["sort"] == "relevance_score:desc"

    pool_lines = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(pool_lines) == 1
    row = pool_lines[0]
    assert row["record_id"] == "oa_000001"
    assert row["source"] == "openalex"
    assert row["topic_query"] == "TDDFT"
    assert row["source_url"] == "https://zenodo.org/records/19917998"


def test_build_stage1_pool_marks_discard_reason_for_filtered_and_merged_rows(tmp_path, monkeypatch):
    raw_rows = [
        sample_work(),
        sample_work() | {"id": "https://openalex.org/W124"},
        sample_work() | {
            "id": "https://openalex.org/W125",
            "display_name": "Reference handbook",
            "type": "book",
        },
    ]

    monkeypatch.setattr(
        "topic_stage1_openalex_pool.fetch_openalex_works",
        lambda **kwargs: raw_rows,
    )
    monkeypatch.setattr(
        "topic_stage1_openalex_pool.current_shanghai_timestamp",
        lambda: "2026-05-03T12:00:00+08:00",
    )

    config = {
        "topic_query": "TDDFT",
        "start_date": "2026-04-26",
        "end_date": "2026-05-03",
        "max_results": 20,
        "outdir": str(tmp_path / "out"),
        "sort": "relevance_score:desc",
    }

    summary = build_stage1_pool(config)

    raw_path = Path(config["outdir"]) / "openalex_2026-05-03T12-00-00.jsonl"
    raw_lines = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert summary["raw"] == 3
    assert summary["pool"] == 1
    assert "discard_reason" not in raw_lines[0]
    assert raw_lines[1]["discard_reason"] == "duplicate_title_normalized"
    assert raw_lines[2]["discard_reason"] == "unsupported_type:book"
