#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch PRL papers from APS RSS and enrich them with best-available abstracts.

Strategy:
- APS RSS is discovery-only.
- Try OpenAlex title search for each RSS item.
- Prefer the formal PRL record when it has an abstract.
- If the formal record has no abstract but a same-title arXiv/preprint record has one,
  allow using that preprint abstract.
- Preserve the RSS snippet separately for debugging / manual fallback inspection.

Usage:
  python3 scripts/prl_rss_extract.py --n 20 --out raw.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional
from urllib.parse import quote

import requests

DEFAULT_FEED_URL = "https://feeds.aps.org/rss/tocsec/PRL-CondensedMatterStructureetc.xml"
RECENT_FEED_URL = "https://feeds.aps.org/rss/recent/prl.xml"
OPENALEX_URL = "https://api.openalex.org/works"
USER_AGENT = "Mozilla/5.0 (Hermes PRL pipeline; +https://openalex.org)"


LATEX_SIMPLE_COMMANDS = [
    "mathrm",
    "mathbb",
    "mathbf",
    "mathit",
    "text",
    "operatorname",
    "rm",
    "cal",
]


def resolve_feed_url(cli_value: str = "") -> str:
    return (cli_value or os.environ.get("PRL_FEED_URL") or DEFAULT_FEED_URL).strip()


def resolve_source(feed_url: str) -> str:
    if "PRL-CondensedMatter" in feed_url or "CondensedMatter" in feed_url:
        return "APS PRL Condensed Matter RSS (feeds.aps.org)"
    return "APS PRL RSS (feeds.aps.org)"


def strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def normalize_spaces(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_items(xml: str) -> List[str]:
    return re.findall(r"<item[\s\S]*?</item>", xml)


def fetch_feed_xml(feed_url: str) -> str:
    return requests.get(feed_url, timeout=30, headers={"User-Agent": USER_AGENT}).text


def get_tag(item: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([\s\S]*?)</{tag}>", item)
    return m.group(1).strip() if m else ""


def latex_to_plain_text(s: str) -> str:
    text = html.unescape(s or "")
    text = text.replace("&nbsp;", " ")
    text = text.replace("–", "-").replace("—", "-")
    for cmd in LATEX_SIMPLE_COMMANDS:
        text = re.sub(rf"\\{cmd}\{{([^{{}}]+)\}}", r"\1", text)
    text = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", text)
    text = re.sub(r"_\{([^{}]+)\}", r"\1", text)
    text = re.sub(r"\^\{([^{}]+)\}", r"\1", text)
    text = text.replace("$", "")
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\\[A-Za-z]+", "", text)
    text = text.replace("_", "")
    text = text.replace("^", "")
    return normalize_spaces(text)


def normalize_title_key(s: str) -> str:
    s = latex_to_plain_text(s).lower()
    s = s.replace("µ", "u")
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def title_similarity(a: str, b: str) -> float:
    ak = normalize_title_key(a)
    bk = normalize_title_key(b)
    if not ak or not bk:
        return 0.0
    if ak == bk:
        return 1.0
    return SequenceMatcher(None, ak, bk).ratio()


def extract_abstract_from_encoded(item: str) -> str:
    enc = get_tag(item, "content:encoded")
    if not enc:
        return ""
    ps = re.findall(r"<p>([\s\S]*?)</p>", enc)
    if not ps:
        return ""
    cand = ps[1] if len(ps) >= 2 else ps[0]
    return normalize_spaces(html.unescape(strip_tags(cand)))


def date_key_from_rss_date(rss_date: str) -> str:
    return normalize_spaces(rss_date)[:10]


def get_item_rss_date(item_xml: str) -> str:
    return html.unescape(strip_tags(get_tag(item_xml, "dc:date")))


def latest_feed_date(items: List[str]) -> str:
    dates = [date_key_from_rss_date(get_item_rss_date(it)) for it in items]
    dates = [d for d in dates if d]
    return max(dates) if dates else ""


def shift_date_key(date_key: str, days_ago: int = 0) -> str:
    base = normalize_spaces(date_key)
    if not base:
        return ""
    try:
        d = dt.datetime.strptime(base[:10], "%Y-%m-%d").date()
    except ValueError:
        return base[:10]
    return (d - dt.timedelta(days=max(0, int(days_ago)))).isoformat()


def filter_items_by_date(items: List[str], target_date: str) -> List[str]:
    target = (target_date or "").strip()
    if not target:
        return list(items)
    return [it for it in items if date_key_from_rss_date(get_item_rss_date(it)) == target]


def looks_truncated(text: str) -> bool:
    s = normalize_spaces(text)
    if not s:
        return True
    return bool(re.search(r"(?:\u2026|\.\.\.|…)$", s) or re.search(r"\b(?:st|di|re|rema)\u2026$", s))


def rebuild_openalex_abstract(inv: dict) -> str:
    if not isinstance(inv, dict):
        return ""
    pairs = []
    for word, positions in inv.items():
        if not isinstance(positions, list):
            continue
        for pos in positions:
            if isinstance(pos, int):
                pairs.append((pos, word))
    if not pairs:
        return ""
    pairs.sort(key=lambda x: x[0])
    return normalize_spaces(" ".join(word for _, word in pairs))


def safe_get_json(url: str, timeout: int = 30) -> dict:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def openalex_search_by_title(title_en: str, per_page: int = 10) -> list[dict]:
    url = f"{OPENALEX_URL}?search={quote(latex_to_plain_text(title_en))}&per-page={per_page}"
    data = safe_get_json(url)
    return data.get("results") or []


def get_work_source_name(work: dict) -> str:
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    return normalize_spaces(source.get("display_name") or work.get("host_venue", {}).get("display_name") or "")


def get_work_doi(work: dict) -> str:
    doi = normalize_spaces(work.get("doi") or "")
    if doi.lower().startswith("https://doi.org/"):
        doi = doi.split("/", 3)[-1]
    return doi


def classify_work_match(work: dict, rss_title: str, rss_doi: str) -> dict:
    title = normalize_spaces(work.get("title") or "")
    doi = get_work_doi(work)
    source_name = get_work_source_name(work)
    work_type = normalize_spaces(work.get("type") or "")
    abstract = rebuild_openalex_abstract(work.get("abstract_inverted_index") or {})
    sim = title_similarity(rss_title, title)
    exact_doi = bool(rss_doi and doi and rss_doi.lower() == doi.lower())
    exact_title = sim >= 0.985
    likely_same_title = sim >= 0.92
    is_prl = "physical review letters" in source_name.lower()
    is_preprint = work_type == "preprint" or "arxiv" in doi.lower() or "arxiv" in source_name.lower()
    return {
        "title": title,
        "doi": doi,
        "source_name": source_name,
        "work_type": work_type,
        "abstract": abstract,
        "has_abstract": bool(abstract),
        "similarity": sim,
        "exact_doi": exact_doi,
        "exact_title": exact_title,
        "likely_same_title": likely_same_title,
        "is_prl": is_prl,
        "is_preprint": is_preprint,
    }


def choose_best_abstract_from_openalex(title_en: str, doi: str) -> dict:
    results = openalex_search_by_title(title_en, per_page=12)
    classified = [classify_work_match(w, title_en, doi) for w in results]

    prl_candidates = [
        c for c in classified
        if (c["exact_doi"] or (c["is_prl"] and c["likely_same_title"]))
    ]
    same_title_preprints = [
        c for c in classified
        if c["is_preprint"] and c["likely_same_title"]
    ]

    chosen = None
    abstract_source = ""

    for c in prl_candidates:
        if c["has_abstract"]:
            chosen = c
            abstract_source = "openalex_prl"
            break

    if not chosen:
        for c in same_title_preprints:
            if c["has_abstract"]:
                chosen = c
                abstract_source = "openalex_arxiv"
                break

    return {
        "results_checked": len(classified),
        "matched_prl_record": any(prl_candidates),
        "matched_prl_with_abstract": any(c["has_abstract"] for c in prl_candidates),
        "matched_same_title_preprint": any(same_title_preprints),
        "matched_same_title_preprint_with_abstract": any(c["has_abstract"] for c in same_title_preprints),
        "chosen": chosen,
        "abstract_source": abstract_source,
        "top_matches": [
            {
                "title": c["title"],
                "doi": c["doi"],
                "source_name": c["source_name"],
                "work_type": c["work_type"],
                "has_abstract": c["has_abstract"],
                "similarity": round(c["similarity"], 4),
                "exact_doi": c["exact_doi"],
                "is_prl": c["is_prl"],
                "is_preprint": c["is_preprint"],
            }
            for c in classified[:5]
        ],
    }


def parse_rss_authors(creator_text: str) -> List[str]:
    text = normalize_spaces(strip_tags(html.unescape(creator_text or "")))
    if not text:
        return []
    text = re.sub(r"\s*,\s*and\s+", ", ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+and\s+", ", ", text, flags=re.IGNORECASE)
    parts = [normalize_spaces(x) for x in text.split(",")]
    return [x for x in parts if x]



def build_item_stub(item_xml: str) -> Dict:
    title = latex_to_plain_text(strip_tags(get_tag(item_xml, "title")))
    doi = html.unescape(strip_tags(get_tag(item_xml, "prism:doi")))
    link = html.unescape(strip_tags(get_tag(item_xml, "link")))
    rss_date = html.unescape(strip_tags(get_tag(item_xml, "dc:date")))
    creator_text = normalize_spaces(strip_tags(html.unescape(get_tag(item_xml, "dc:creator"))))
    authors = parse_rss_authors(creator_text)
    rss_snippet = extract_abstract_from_encoded(item_xml)

    return {
        "title_en": title,
        "doi": doi,
        "link": link,
        "rss_date": rss_date,
        "author_text": creator_text,
        "authors": authors,
        "first_author": authors[0] if authors else "",
        "rss_snippet": rss_snippet,
        "rss_snippet_truncated": looks_truncated(rss_snippet),
    }


def enrich_item_payload(base_item: Dict) -> Dict:
    title = base_item.get("title_en") or ""
    doi = base_item.get("doi") or ""
    rss_snippet = normalize_spaces(base_item.get("rss_snippet") or "")

    oa = choose_best_abstract_from_openalex(title, doi)
    chosen = oa.get("chosen") or {}
    openalex_abstract = normalize_spaces(chosen.get("abstract") or "")
    if openalex_abstract:
        abstract_en = openalex_abstract
        abstract_source = oa.get("abstract_source") or ""
    else:
        abstract_en = rss_snippet
        abstract_source = "rss_snippet" if rss_snippet else ""

    return {
        **base_item,
        "abstract_en": abstract_en,
        "abstract_source": abstract_source,
        "matched_prl_record": oa.get("matched_prl_record", False),
        "matched_prl_with_abstract": oa.get("matched_prl_with_abstract", False),
        "matched_same_title_preprint": oa.get("matched_same_title_preprint", False),
        "matched_same_title_preprint_with_abstract": oa.get("matched_same_title_preprint_with_abstract", False),
        "openalex_results_checked": oa.get("results_checked", 0),
        "openalex_top_matches": oa.get("top_matches", []),
        "missing_any_abstract": not bool(abstract_en),
    }


def build_item_payload(item_xml: str) -> Dict:
    return enrich_item_payload(build_item_stub(item_xml))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--out", required=True)
    ap.add_argument("--feed-url", default="", help="Override APS RSS feed URL; defaults to PRL condensed-matter feed")
    args = ap.parse_args()

    date_local = dt.datetime.now().strftime("%Y-%m-%d")
    feed_url = resolve_feed_url(args.feed_url)
    source = resolve_source(feed_url)

    xml = fetch_feed_xml(feed_url)
    items = parse_items(xml)
    if not items:
        raise SystemExit("No RSS items found")

    out_items: List[Dict] = []
    for idx, it in enumerate(items[: max(1, args.n)], start=1):
        out_items.append(build_item_payload(it))
        if idx < min(len(items), max(1, args.n)):
            time.sleep(0.15)

    payload = {
        "date": date_local,
        "source": source,
        "feed_url": feed_url,
        "item_count_in_feed": len(items),
        "items": out_items,
    }

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
