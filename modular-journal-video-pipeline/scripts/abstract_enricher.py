from difflib import SequenceMatcher

from arxiv_client import find_best_arxiv_match, search_arxiv_by_title
from openalex_client import get_work_doi, get_work_source_name, reconstruct_abstract, search_openalex_by_title


def title_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, " ".join((left or '').lower().split()), " ".join((right or '').lower().split())).ratio()


def classify_openalex_match(work: dict, target_title: str, target_doi: str) -> dict:
    title = (work.get('display_name') or work.get('title') or '').strip()
    doi = get_work_doi(work)
    source_name = get_work_source_name(work)
    work_type = (work.get('type') or '').strip().lower()
    abstract = reconstruct_abstract(work.get('abstract_inverted_index') or {})
    sim = title_similarity(target_title, title)
    exact_doi = bool(target_doi and doi and target_doi.lower() == doi.lower())
    likely_same_title = sim >= 0.92
    is_physics_journal = any(token in source_name.lower() for token in ['physical review', 'nature', 'science'])
    is_preprint = work_type == 'preprint' or 'arxiv' in doi.lower() or 'arxiv' in source_name.lower()
    return {
        'title': title,
        'doi': doi,
        'source_name': source_name,
        'work_type': work_type,
        'abstract': abstract,
        'has_abstract': bool(abstract),
        'similarity': sim,
        'exact_doi': exact_doi,
        'likely_same_title': likely_same_title,
        'is_physics_journal': is_physics_journal,
        'is_preprint': is_preprint,
    }


def choose_abstract_candidate(title: str, doi: str, *, openalex_search_fn=None, arxiv_search_fn=None) -> dict:
    openalex_results = (openalex_search_fn or search_openalex_by_title)(title, per_page=12)
    classified = [classify_openalex_match(work, title, doi) for work in openalex_results]

    for candidate in classified:
        if candidate['has_abstract'] and (candidate['exact_doi'] or (candidate['is_physics_journal'] and candidate['likely_same_title'])):
            return {'abstract': candidate['abstract'], 'source': 'openalex', 'match': candidate}

    for candidate in classified:
        if candidate['has_abstract'] and candidate['is_preprint'] and candidate['likely_same_title']:
            return {'abstract': candidate['abstract'], 'source': 'openalex_preprint', 'match': candidate}

    arxiv_entries = (arxiv_search_fn or search_arxiv_by_title)(title, max_results=5)
    best_arxiv = find_best_arxiv_match(title, arxiv_entries)
    if best_arxiv and (best_arxiv.get('abstract') or '').strip():
        return {'abstract': (best_arxiv.get('abstract') or '').strip(), 'source': 'arxiv', 'match': best_arxiv}

    return {'abstract': '', 'source': '', 'match': None}


def enrich_abstract_from_title(row: dict, config: dict, *, openalex_search_fn=None, arxiv_search_fn=None) -> dict | None:
    title = (row.get('paper_title') or '').strip()
    if not title:
        return None
    doi = (row.get('paper_doi') or '').strip()
    result = choose_abstract_candidate(
        title,
        doi,
        openalex_search_fn=openalex_search_fn,
        arxiv_search_fn=arxiv_search_fn,
    )
    abstract = (result.get('abstract') or '').strip()
    if not abstract:
        return None
    return {'abstract': abstract, 'source': result.get('source') or 'enriched'}
