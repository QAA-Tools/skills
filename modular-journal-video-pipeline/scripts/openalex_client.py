import html
import re
import unicodedata
from datetime import date, datetime

import requests

OPENALEX_URL = "https://api.openalex.org/works"
OPENALEX_PAGE_SIZE = 25
OPENALEX_TIMEOUT = 60
USER_AGENT = "HermesAgent/1.0 (modular journal pipeline OpenAlex client)"
ALLOWED_OPENALEX_TYPES = {"article"}
SEPARATOR_TRANSLATION = str.maketrans({
    "_": " ",
    "-": " ",
    "‐": " ",
    "‑": " ",
    "‒": " ",
    "–": " ",
    "—": " ",
    "/": " ",
    ":": " ",
    ",": " ",
    ".": " ",
    ";": " ",
    "(": " ",
    ")": " ",
    "[": " ",
    "]": " ",
    "{": " ",
    "}": " ",
})


def normalize_title(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = html.unescape(normalized)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = normalized.lower().strip()
    normalized = normalized.translate(SEPARATOR_TRANSLATION)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def reconstruct_abstract(inverted_index: dict | None) -> str:
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted_index.items():
        for idx in indexes:
            positions.append((idx, word))
    positions.sort()
    return " ".join(word for _, word in positions).strip()


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def canonical_doi_url(doi: str) -> str:
    clean = (doi or "").strip()
    if not clean:
        return ""
    return f"https://doi.org/{clean}"


def get_work_source_name(work: dict) -> str:
    primary_location = work.get("primary_location") or {}
    source = primary_location.get("source") or {}
    return (source.get("display_name") or "").strip()


def get_work_doi(work: dict) -> str:
    doi = (work.get("doi") or "").strip()
    if doi.startswith("https://doi.org/"):
        doi = doi.replace("https://doi.org/", "", 1)
    return doi


def inspect_openalex_work(work: dict, *, topic_query: str, start_date: str, end_date: str, retrieved_at: str) -> tuple[dict | None, str | None]:
    work_type = (work.get("type") or "").strip().lower()
    if work_type and work_type not in ALLOWED_OPENALEX_TYPES:
        return None, f"unsupported_type:{work_type}"

    publication_date = (work.get("publication_date") or "").strip()
    if not publication_date:
        return None, "missing_publication_date"
    pub_date = parse_date(publication_date)
    if pub_date < parse_date(start_date) or pub_date > parse_date(end_date):
        return None, "outside_date_window"

    title = (work.get("display_name") or work.get("title") or "").strip()
    title_normalized = normalize_title(title)
    if not title_normalized:
        return None, "empty_normalized_title"

    doi = get_work_doi(work)
    primary_location = work.get("primary_location") or {}
    landing_page_url = (primary_location.get("landing_page_url") or "").strip()
    authors = []
    for authorship in work.get("authorships") or []:
        author_name = (((authorship.get("author") or {}).get("display_name")) or "").strip()
        if author_name:
            authors.append(author_name)

    openalex_id = (work.get("id") or "").strip()
    return {
        "source": "openalex",
        "openalex_id": openalex_id,
        "topic_query": topic_query,
        "start_date": start_date,
        "end_date": end_date,
        "retrieved_at": retrieved_at,
        "title": title,
        "title_normalized": title_normalized,
        "abstract": reconstruct_abstract(work.get("abstract_inverted_index")),
        "publication_date": publication_date,
        "journal": get_work_source_name(work),
        "doi": doi,
        "paper_url": canonical_doi_url(doi) or landing_page_url,
        "source_url": landing_page_url,
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "raw_source_ids": [openalex_id] if openalex_id else [],
        "raw_duplicate_count": 1,
    }, None



def parse_openalex_work(work: dict, *, topic_query: str, start_date: str, end_date: str, retrieved_at: str) -> dict | None:
    record, _ = inspect_openalex_work(
        work,
        topic_query=topic_query,
        start_date=start_date,
        end_date=end_date,
        retrieved_at=retrieved_at,
    )
    return record


def fetch_openalex_works(
    topic_query: str,
    start_date: str,
    end_date: str,
    max_results: int,
    *,
    sort: str = "relevance_score:desc",
    http_get=None,
) -> list[dict]:
    getter = http_get or requests.get
    rows: list[dict] = []
    page = 1
    while len(rows) < max_results:
        response = getter(
            OPENALEX_URL,
            params={
                "search": topic_query,
                "filter": f"from_publication_date:{start_date},to_publication_date:{end_date}",
                "sort": sort,
                "page": page,
                "per-page": OPENALEX_PAGE_SIZE,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=OPENALEX_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") or []
        if not results:
            break
        rows.extend(results)
        page += 1
    return rows[:max_results]


def search_openalex_by_title(title: str, *, per_page: int = 12, http_get=None) -> list[dict]:
    getter = http_get or requests.get
    response = getter(
        OPENALEX_URL,
        params={"search": title, "per-page": per_page},
        headers={"User-Agent": USER_AGENT},
        timeout=OPENALEX_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("results") or []
