import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from abstract_enricher import choose_abstract_candidate, enrich_abstract_from_title  # noqa: E402
from topic_stage2_score_and_enrich import run_stage2, write_jsonl  # noqa: E402


def sample_openalex_work(*, title: str, journal: str = "Physical Review Letters", doi: str = "", abstract_words=None, work_type: str = "article"):
    if abstract_words is None:
        abstract_words = ["Recovered", "from", "OpenAlex"]
    return {
        "id": "https://openalex.org/W1",
        "type": work_type,
        "display_name": title,
        "doi": doi,
        "abstract_inverted_index": {word: [idx] for idx, word in enumerate(abstract_words)},
        "primary_location": {"source": {"display_name": journal}},
    }


def sample_stage2_work(*, openalex_id: str, title: str, journal: str = "Physical Review Letters", abstract_words=None):
    if abstract_words is None:
        abstract_words = []
    return {
        "id": openalex_id,
        "type": "article",
        "publication_date": "2026-05-03",
        "display_name": title,
        "doi": "",
        "abstract_inverted_index": {word: [idx] for idx, word in enumerate(abstract_words)},
        "primary_location": {
            "landing_page_url": f"https://example.org/{openalex_id.split('/')[-1]}",
            "source": {"display_name": journal},
        },
        "authorships": [{"author": {"display_name": "Xi Zhang"}}],
    }


def test_choose_abstract_candidate_prefers_openalex_physics_journal_match():
    def fake_openalex_search(title: str, *, per_page: int):
        return [sample_openalex_work(title=title, journal="Physical Review Letters")]

    result = choose_abstract_candidate(
        "A TDDFT Study",
        "",
        openalex_search_fn=fake_openalex_search,
        arxiv_search_fn=lambda title, *, max_results: [],
    )
    assert result["source"] == "openalex"
    assert result["abstract"] == "Recovered from OpenAlex"


def test_choose_abstract_candidate_falls_back_to_arxiv_when_openalex_has_no_abstract():
    def fake_openalex_search(title: str, *, per_page: int):
        return [sample_openalex_work(title=title, abstract_words=[])]

    def fake_arxiv_search(title: str, *, max_results: int):
        return [{"title": title, "abstract": "Recovered from arXiv", "arxiv_id": "http://arxiv.org/abs/1"}]

    result = choose_abstract_candidate(
        "A TDDFT Study",
        "",
        openalex_search_fn=fake_openalex_search,
        arxiv_search_fn=fake_arxiv_search,
    )
    assert result["source"] == "arxiv"
    assert result["abstract"] == "Recovered from arXiv"


def test_run_stage2_uses_default_abstract_enricher_tool_when_enabled(tmp_path, monkeypatch):
    openalex_file = tmp_path / "openalex_2026-05-03T16-00-00.jsonl"
    write_jsonl(openalex_file, [sample_stage2_work(openalex_id="https://openalex.org/W2", title="Missing Abstract", abstract_words=[])])
    pool_path = tmp_path / "topic_paper_pool.jsonl"

    def fake_default_enricher(row: dict, config: dict):
        return {"abstract": f"Recovered for {row['paper_title']}", "source": "arxiv"}

    monkeypatch.setattr("topic_stage2_score_and_enrich.enrich_abstract_from_title", fake_default_enricher)

    summary = run_stage2(
        {
            "openalex_inputs": [str(openalex_file)],
            "pool_path": str(pool_path),
            "journal_ai_threshold": 0.8,
            "journal_impact_threshold": 5.0,
            "score_sample_count": 1,
            "enable_abstract_enrichment": True,
            "journal_ai_model": "fake-ai-model",
            "journal_impact_model": "fake-impact-model",
        },
        score_journal_fn=lambda journal_name, score_kind, sample_index, model_name: 0.9 if score_kind == "ai" else 6.0,
    )

    rows = [json.loads(line) for line in pool_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert summary["status"] == "OK_NO_REMAINING"
    assert rows[0]["paper_abstract"] == "Recovered for Missing Abstract"
    assert rows[0]["paper_abstract_source"] == "arxiv"
    assert rows[0]["paper_abstract_lookup_status"] == "done"