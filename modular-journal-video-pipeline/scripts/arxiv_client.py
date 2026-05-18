import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import requests

ARXIV_API_URL = "http://export.arxiv.org/api/query"
ARXIV_TIMEOUT = 60
USER_AGENT = "HermesAgent/1.0 (modular journal pipeline arXiv client)"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def normalize_arxiv_title(text: str) -> str:
    return " ".join((text or "").lower().split())


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_arxiv_title(left), normalize_arxiv_title(right)).ratio()


def parse_arxiv_feed(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    rows = []
    for entry in root.findall('atom:entry', ATOM_NS):
        title = " ".join((entry.findtext('atom:title', default='', namespaces=ATOM_NS) or '').split())
        summary = " ".join((entry.findtext('atom:summary', default='', namespaces=ATOM_NS) or '').split())
        arxiv_id = (entry.findtext('atom:id', default='', namespaces=ATOM_NS) or '').strip()
        published = (entry.findtext('atom:published', default='', namespaces=ATOM_NS) or '').strip()
        rows.append({
            'title': title,
            'abstract': summary,
            'arxiv_id': arxiv_id,
            'published': published,
        })
    return rows


def search_arxiv_by_title(title: str, *, max_results: int = 5, http_get=None) -> list[dict]:
    getter = http_get or requests.get
    query = f'ti:"{title}"'
    response = getter(
        f'{ARXIV_API_URL}?search_query={quote_plus(query)}&start=0&max_results={max_results}',
        headers={'User-Agent': USER_AGENT},
        timeout=ARXIV_TIMEOUT,
    )
    response.raise_for_status()
    return parse_arxiv_feed(response.text)


def find_best_arxiv_match(title: str, entries: list[dict], *, min_similarity: float = 0.92) -> dict | None:
    best = None
    best_score = 0.0
    for entry in entries:
        score = title_similarity(title, entry.get('title') or '')
        if score > best_score:
            best = dict(entry)
            best['title_similarity'] = score
            best_score = score
    if best_score < min_similarity:
        return None
    return best
