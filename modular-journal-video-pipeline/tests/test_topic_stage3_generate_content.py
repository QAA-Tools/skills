import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from stage3_llm_core import (  # noqa: E402
    build_stage3_page_prompt,
    build_stage3_title_prompt,
    build_stage3_voice_prompt,
    generate_one_paper,
    validate_stage3_page_payload,
    validate_stage3_title_payload,
    validate_stage3_voice_payload,
)
from topic_stage3_generate_content import (  # noqa: E402
    build_other_papers,
    load_stage3_config,
    normalize_stage2_row,
    run_stage3,
    sort_and_select_rows,
)


def sample_stage2_rows():
    return [
        {
            "paper_id": "paper_1",
            "paper_title": "High impact TDDFT paper",
            "paper_abstract": "TDDFT study of excitons in organic materials.",
            "paper_doi": "10.1/a",
            "paper_url": "https://doi.org/10.1/a",
            "paper_journal": "Nature",
            "publication_date": "2026-05-03",
            "authors": ["A", "B"],
            "score_journal_ai": 0.98,
            "score_journal_impact": 50.0,
            "content_brief": "",
            "content_key_points": [],
            "content_title_zh": "",
            "content_voice_intro": "",
            "content_voice_points": [],
            "video_done": False,
        },
        {
            "paper_id": "paper_2",
            "paper_title": "Strong exciton paper",
            "paper_abstract": "Organic exciton transport with TDDFT analysis.",
            "paper_doi": "10.1/b",
            "paper_url": "https://doi.org/10.1/b",
            "paper_journal": "PRL",
            "publication_date": "2026-05-02",
            "authors": ["C"],
            "score_journal_ai": 0.95,
            "score_journal_impact": 9.0,
            "content_brief": "",
            "content_key_points": [],
            "content_title_zh": "",
            "content_voice_intro": "",
            "content_voice_points": [],
            "video_done": False,
        },
        {
            "paper_id": "paper_3",
            "paper_title": "Lower score but better match",
            "paper_abstract": "Exciton organic moire coupling and twisted bilayer study.",
            "paper_doi": "10.1/c",
            "paper_url": "https://doi.org/10.1/c",
            "paper_journal": "JCTC",
            "publication_date": "2026-05-01",
            "authors": ["D"],
            "score_journal_ai": 0.60,
            "score_journal_impact": 5.0,
            "content_brief": "",
            "content_key_points": [],
            "content_title_zh": "",
            "content_voice_intro": "",
            "content_voice_points": [],
            "video_done": False,
        },
    ]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_sort_and_select_rows_uses_stage2_scores_then_topic_match_then_input_order():
    normalized = [normalize_stage2_row(row, index=i, topic_query="exciton organic") for i, row in enumerate(sample_stage2_rows())]
    selected, other = sort_and_select_rows(normalized, selected_n=2)
    assert [row["record_id"] for row in selected] == ["paper_1", "paper_2"]
    assert [row["record_id"] for row in other] == ["paper_3"]


def test_build_other_papers_keeps_unselected_rows_minimal():
    normalized = [normalize_stage2_row(row, index=i, topic_query="exciton organic") for i, row in enumerate(sample_stage2_rows())]
    _, other = sort_and_select_rows(normalized, selected_n=1)
    other_papers = build_other_papers(other)
    assert other_papers == [
        {"record_id": "paper_2", "title_en": "Strong exciton paper", "title_zh": "", "doi": "10.1/b"},
        {"record_id": "paper_3", "title_en": "Lower score but better match", "title_zh": "", "doi": "10.1/c"},
    ]


def test_load_stage3_config_validates_new_contract(tmp_path):
    stage2_final = tmp_path / "pool_stage2_final.jsonl"
    stage2_final.write_text("{}\n", encoding="utf-8")
    outdir = tmp_path / "out"
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "topic_query": "exciton organic",
                "stage2_final": str(stage2_final),
                "outdir": str(outdir),
                "selected_n": 2,
                "llm_mode": "fake",
                "write_debug_artifacts": True,
                "content_model": "fake-content-model",
            }
        ),
        encoding="utf-8",
    )
    config = load_stage3_config(config_path)
    assert config["stage2_final"] == str(stage2_final)
    assert config["outdir"] == str(outdir)
    assert config["llm_mode"] == "fake"
    assert config["selected_n"] == 2
    assert config["process_batch_limit"] == 5
    assert config["write_debug_artifacts"] is True

    bad_path = tmp_path / "bad.json"
    bad_path.write_text(
        json.dumps(
            {
                "topic_query": "x",
                "stage2_final": str(stage2_final),
                "outdir": str(outdir),
                "selected_n": 21,
                "llm_mode": "weird",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="selected_n|llm_mode"):
        load_stage3_config(bad_path)


def test_prompt_builders_are_topic_neutral_not_prl_named():
    item = {"title_en": "A Title", "abstract_en": "An abstract about excitons.", "doi": "10.1/x"}
    page_prompt = build_stage3_page_prompt(item)
    voice_prompt = build_stage3_voice_prompt(item)
    title_prompt = build_stage3_title_prompt(item)
    assert "PRL" not in page_prompt
    assert "PRL" not in voice_prompt
    assert "PRL" not in title_prompt
    assert "4~6" in page_prompt or "4-6" in page_prompt or "4 到 6" in page_prompt
    assert "1~2" in voice_prompt or "1-2" in voice_prompt or "1 到 2" in voice_prompt
    assert "A Title" in page_prompt
    assert "An abstract about excitons." in title_prompt


def test_stage3_validators_enforce_contract():
    assert validate_stage3_title_payload("激子输运的理论研究") == {"title_zh": "激子输运的理论研究"}
    assert validate_stage3_page_payload("第一句。\n第二句。\n第三句。\n第四句。") == {
        "key_points": ["第一句。", "第二句。", "第三句。", "第四句。"]
    }
    assert validate_stage3_voice_payload({"voice_intro": "这是导语。", "voice_points": ["第一点。", "第二点。"]}) == {
        "voice_intro": "这是导语。",
        "voice_points": ["第一点。", "第二点。"],
    }
    assert validate_stage3_page_payload({"key_points": ["key_points", "第一句。", "第二句。", "第三句。"]}) is None
    assert validate_stage3_page_payload({"key_points": ["第一句。", "第二句。", "第三句。"]}) is None
    assert validate_stage3_voice_payload({"voice_intro": "这是导语。", "voice_points": ["第一点。", "第二点。", "第三点。"]}) is None


def test_generate_one_paper_uses_only_page_voice_title_requests():
    row = normalize_stage2_row(sample_stage2_rows()[0], index=0, topic_query="exciton organic")
    calls = []

    def fake_request(prompt: str, validator, *, label: str, paper_title_en: str, doi: str, model_name: str):
        calls.append((label.split(":", 1)[0], model_name, paper_title_en, doi))
        if label.startswith("page:"):
            return {"key_points": ["第一点。", "第二点。", "第三点。", "第四点。"]}
        if label.startswith("voice:"):
            return {"voice_intro": "这是一句简介。", "voice_points": ["第一点。", "第二点。"]}
        if label.startswith("title:"):
            return {"title_zh": "中文标题"}
        raise AssertionError(label)

    paper = generate_one_paper(row, request_json_fn=fake_request, model_name="fake-model")
    assert paper["brief"] == "这是一句简介。"
    assert paper["voice_intro"] == "这是一句简介。"
    assert paper["key_points"] == ["第一点。", "第二点。", "第三点。", "第四点。"]
    assert paper["voice_points"] == ["第一点。", "第二点。"]
    assert [item[0] for item in calls] == ["page", "voice", "title"]
    assert all(item[1] == "fake-model" for item in calls)


def test_run_stage3_writes_expected_output_and_failed_papers(tmp_path):
    stage2_final = tmp_path / "pool_stage2_final.jsonl"
    outdir = tmp_path / "stage3_out"
    write_jsonl(stage2_final, sample_stage2_rows())
    config = {
        "topic_query": "exciton organic",
        "stage2_final": str(stage2_final),
        "outdir": str(outdir),
        "selected_n": 2,
        "llm_mode": "fake",
        "write_debug_artifacts": True,
        "content_model": "fake-content-model",
    }

    def fake_generate_one(row: dict, *, request_json_fn=None, model_name: str):
        assert model_name == "fake-content-model"
        if row["record_id"] == "paper_2":
            raise RuntimeError("voice generation failed")
        return {
            "record_id": row["record_id"],
            "title_en": row["title_en"],
            "title_zh": "中文标题",
            "doi": row["doi"],
            "paper_url": row["paper_url"],
            "brief": "这是一句简介。",
            "key_points": ["第一点。", "第二点。", "第三点。", "第四点。"],
            "voice_intro": "这是一句简介。",
            "voice_points": ["第一点。", "第二点。"],
        }

    summary = run_stage3(config, generate_one_paper_fn=fake_generate_one)
    out = json.loads((outdir / "input_stage3.json").read_text(encoding="utf-8"))
    preview = json.loads((outdir / "stage3_selected_preview.json").read_text(encoding="utf-8"))

    assert summary["stage2_rows"] == 3
    assert summary["selected"] == 2
    assert summary["generated"] == 1
    assert summary["failed"] == 1
    assert summary["other_papers"] == 1
    assert preview["selected_record_ids"] == ["paper_1", "paper_2"]
    assert len(out["papers"]) == 1
    assert out["papers"][0]["brief"] == out["papers"][0]["voice_intro"]
    assert len(out["papers"][0]["key_points"]) == 4
    assert len(out["papers"][0]["voice_points"]) == 2
    assert out["failed_papers"] == [
        {
            "record_id": "paper_2",
            "title_en": "Strong exciton paper",
            "doi": "10.1/b",
            "failure_stage": "voice",
            "failure_reason": "voice generation failed",
        }
    ]
    assert out["other_papers"] == [
        {"record_id": "paper_3", "title_en": "Lower score but better match", "title_zh": "", "doi": "10.1/c"}
    ]


def test_run_stage3_resumes_from_its_own_output_json_without_mutating_stage2_input(tmp_path):
    stage2_final = tmp_path / "pool_stage2_final.jsonl"
    outdir = tmp_path / "stage3_out"
    original_rows = sample_stage2_rows()
    write_jsonl(stage2_final, original_rows)
    config = {
        "topic_query": "exciton organic",
        "stage2_final": str(stage2_final),
        "outdir": str(outdir),
        "selected_n": 2,
        "process_batch_limit": 1,
        "llm_mode": "fake",
        "write_debug_artifacts": False,
        "content_model": "fake-content-model",
    }
    calls = []

    def fake_generate_one(row: dict, *, request_json_fn=None, model_name: str):
        calls.append(row["record_id"])
        return {
            "record_id": row["record_id"],
            "title_en": row["title_en"],
            "title_zh": f"中文标题-{row['record_id']}",
            "doi": row["doi"],
            "paper_url": row["paper_url"],
            "brief": f"简介-{row['record_id']}",
            "key_points": ["第一点。", "第二点。", "第三点。", "第四点。"],
            "voice_intro": f"简介-{row['record_id']}",
            "voice_points": ["补充一点。", "补充二点。"],
        }

    summary1 = run_stage3(config, generate_one_paper_fn=fake_generate_one)
    rows_after_first_run = [json.loads(line) for line in stage2_final.read_text(encoding="utf-8").splitlines() if line.strip()]
    out1 = json.loads((outdir / "input_stage3.json").read_text(encoding="utf-8"))

    assert calls == ["paper_1"]
    assert summary1["status"] == "OK_PROGRESS"
    assert summary1["processed_this_run"] == 1
    assert summary1["remaining_to_process"] == 1
    assert summary1["generated"] == 1
    assert rows_after_first_run == original_rows
    assert [paper["record_id"] for paper in out1["papers"]] == ["paper_1"]

    summary2 = run_stage3(config, generate_one_paper_fn=fake_generate_one)
    rows_after_second_run = [json.loads(line) for line in stage2_final.read_text(encoding="utf-8").splitlines() if line.strip()]
    out2 = json.loads((outdir / "input_stage3.json").read_text(encoding="utf-8"))

    assert calls == ["paper_1", "paper_2"]
    assert summary2["status"] == "OK_NO_REMAINING"
    assert summary2["processed_this_run"] == 1
    assert summary2["remaining_to_process"] == 0
    assert summary2["generated"] == 2
    assert rows_after_second_run == original_rows
    assert [paper["record_id"] for paper in out2["papers"]] == ["paper_1", "paper_2"]


def test_run_stage3_skips_existing_complete_and_reason_blocked_items(tmp_path):
    stage2_final = tmp_path / "pool_stage2_final.jsonl"
    outdir = tmp_path / "stage3_out"
    write_jsonl(stage2_final, sample_stage2_rows())
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "input_stage3.json").write_text(
        json.dumps(
            {
                "topic_query": "exciton organic",
                "selected_n": 2,
                "generated_at": "2026-05-07T00:00:00+08:00",
                "papers": [
                    {
                        "record_id": "paper_1",
                        "title_en": "High impact TDDFT paper",
                        "title_zh": "已有中文标题",
                        "doi": "10.1/a",
                        "paper_url": "https://doi.org/10.1/a",
                        "brief": "已有简介",
                        "key_points": ["第一点。", "第二点。", "第三点。", "第四点。"],
                        "voice_intro": "已有简介",
                        "voice_points": ["第一点。", "第二点。"],
                    },
                    {
                        "record_id": "paper_2",
                        "title_en": "Strong exciton paper",
                        "title_zh": "",
                        "doi": "10.1/b",
                        "paper_url": "https://doi.org/10.1/b",
                        "brief": "",
                        "key_points": [],
                        "voice_intro": "",
                        "voice_points": [],
                    },
                ],
                "other_papers": [],
                "failed_papers": [
                    {
                        "record_id": "paper_2",
                        "title_en": "Strong exciton paper",
                        "doi": "10.1/b",
                        "failure_stage": "page",
                        "failure_reason": "not found",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    config = {
        "topic_query": "exciton organic",
        "stage2_final": str(stage2_final),
        "outdir": str(outdir),
        "selected_n": 2,
        "process_batch_limit": 5,
        "llm_mode": "fake",
        "write_debug_artifacts": False,
        "content_model": "fake-content-model",
    }

    def should_not_be_called(row: dict, *, request_json_fn=None, model_name: str):
        raise AssertionError(f"unexpected regenerate for {row['record_id']}")

    summary = run_stage3(config, generate_one_paper_fn=should_not_be_called)
    out = json.loads((outdir / "input_stage3.json").read_text(encoding="utf-8"))

    assert summary["status"] == "OK_NO_REMAINING"
    assert summary["processed_this_run"] == 0
    assert summary["generated"] == 1
    assert summary["failed"] == 1
    assert summary["remaining_to_process"] == 0
    assert [paper["record_id"] for paper in out["papers"]] == ["paper_1"]
    assert out["failed_papers"] == [
        {
            "record_id": "paper_2",
            "title_en": "Strong exciton paper",
            "doi": "10.1/b",
            "failure_stage": "page",
            "failure_reason": "not found",
        }
    ]


def test_run_stage3_raises_when_zero_papers_succeed(tmp_path):
    stage2_final = tmp_path / "pool_stage2_final.jsonl"
    outdir = tmp_path / "stage3_out"
    write_jsonl(stage2_final, sample_stage2_rows()[:1])
    config = {
        "topic_query": "exciton organic",
        "stage2_final": str(stage2_final),
        "outdir": str(outdir),
        "selected_n": 1,
        "llm_mode": "fake",
        "write_debug_artifacts": False,
        "content_model": "fake-content-model",
    }

    def always_fail(row: dict, *, request_json_fn=None, model_name: str):
        raise RuntimeError("page generation failed")

    with pytest.raises(RuntimeError, match="0 papers succeeded|no papers succeeded"):
        run_stage3(config, generate_one_paper_fn=always_fail)
