import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from arxiv_client import find_best_arxiv_match, parse_arxiv_feed, search_arxiv_by_title  # noqa: E402


ARXIV_XML = """<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns='http://www.w3.org/2005/Atom'>
  <entry>
    <id>http://arxiv.org/abs/1234.5678v1</id>
    <published>2026-05-03T00:00:00Z</published>
    <title>A TDDFT Study</title>
    <summary>Recovered from arXiv summary.</summary>
  </entry>
</feed>
"""


def test_parse_arxiv_feed_and_pick_best_title_match():
    entries = parse_arxiv_feed(ARXIV_XML)
    assert len(entries) == 1
    assert entries[0]["title"] == "A TDDFT Study"
    assert entries[0]["abstract"] == "Recovered from arXiv summary."

    best = find_best_arxiv_match("A TDDFT Study", entries)
    assert best is not None
    assert best["arxiv_id"] == "http://arxiv.org/abs/1234.5678v1"
    assert best["title_similarity"] >= 0.99


def test_search_arxiv_by_title_uses_injected_http_get():
    calls = []

    class FakeResponse:
        text = ARXIV_XML

        def raise_for_status(self):
            return None

    def fake_get(url, *, headers, timeout):
        calls.append((url, headers, timeout))
        return FakeResponse()

    entries = search_arxiv_by_title("A TDDFT Study", http_get=fake_get)
    assert len(entries) == 1
    assert "search_query" in calls[0][0]
    assert calls[0][2] == 60