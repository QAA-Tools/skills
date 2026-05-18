import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from openalex_client import fetch_openalex_works, normalize_title, parse_openalex_work, reconstruct_abstract  # noqa: E402


def sample_openalex_work(*, openalex_id: str, title: str, abstract_words=None, doi: str = "", journal: str = "Physical Review Letters"):
    if abstract_words is None:
        abstract_words = ["This", "work", "studies", "TDDFT"]
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


def test_openalex_client_normalizes_title_and_reconstructs_abstract():
    assert normalize_title("TDDFT: A Study") == "tddft a study"
    assert reconstruct_abstract({"TDDFT": [1], "Study": [0]}) == "Study TDDFT"


def test_parse_openalex_work_returns_reusable_record_shape():
    parsed = parse_openalex_work(
        sample_openalex_work(openalex_id="https://openalex.org/W1", title="A TDDFT Study", doi="10.1000/xyz"),
        topic_query="",
        start_date="2026-05-01",
        end_date="2026-05-31",
        retrieved_at="2026-05-04T10:00:00+08:00",
    )
    assert parsed is not None
    assert parsed["title_normalized"] == "a tddft study"
    assert parsed["journal"] == "Physical Review Letters"
    assert parsed["paper_url"] == "https://doi.org/10.1000/xyz"


def test_fetch_openalex_works_uses_injected_http_get_and_respects_max_results():
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, *, params, headers, timeout):
        calls.append((url, params, headers, timeout))
        if params["page"] == 1:
            payload = {"results": [sample_openalex_work(openalex_id="https://openalex.org/W1", title="P1")]}
        else:
            payload = {"results": [sample_openalex_work(openalex_id="https://openalex.org/W2", title="P2")]}
        return FakeResponse(payload)

    rows = fetch_openalex_works(
        topic_query="TDDFT",
        start_date="2026-05-01",
        end_date="2026-05-31",
        max_results=2,
        sort="relevance_score:desc",
        http_get=fake_get,
    )
    assert len(rows) == 2
    assert calls[0][1]["search"] == "TDDFT"
    assert calls[0][1]["sort"] == "relevance_score:desc"
    assert calls[0][3] == 60