#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate PRL video from RSS with optional auto-fill for input.json.

Flow:
1) Extract raw RSS data (English title + abstract snippet)
2) Print a JSON "fill task" prompt for an LLM to produce the final render input
3) Optionally auto-fill input.json (fake mode by default; API mode optional)
4) If --filled is provided or auto-fill runs, render the video with render_prl.py

Usage:
  python3 scripts/make_prl_video_llm.py --n 5 --outdir OUTDIR
  # then run your LLM to produce OUTDIR/input.json
  python3 scripts/make_prl_video_llm.py --outdir OUTDIR --filled OUTDIR/input.json
  # or auto-fill + render in one step (default PRL_LLM_MODE=fake)
  python3 scripts/make_prl_video_llm.py --n 5 --outdir OUTDIR --auto-fill --render
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime_logger import log_runtime_event

import prl_rss_extract as rss_extract


def run(cmd: list):
    subprocess.run(cmd, check=True)


def shanghai_today() -> str:
    return dt.datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def raw_item_key(item: dict) -> str:
    doi = squeeze_spaces((item.get("doi") or "").lower())
    if doi:
        return f"doi:{doi}"
    title_key = rss_extract.normalize_title_key(item.get("title_en") or "")
    return f"title:{title_key}" if title_key else ""


def build_daily_raw(selected_n: int, recent_n: int = 25, days_ago: int = 0) -> dict:
    condensed_url = rss_extract.DEFAULT_FEED_URL
    recent_url = rss_extract.RECENT_FEED_URL

    condensed_xml = rss_extract.fetch_feed_xml(condensed_url)
    recent_xml = rss_extract.fetch_feed_xml(recent_url)
    condensed_items_xml = rss_extract.parse_items(condensed_xml)
    recent_items_xml = rss_extract.parse_items(recent_xml)
    if not condensed_items_xml:
        raise RuntimeError("No condensed-matter RSS items found")
    if not recent_items_xml:
        raise RuntimeError("No recent RSS items found")

    condensed_latest_date = rss_extract.latest_feed_date(condensed_items_xml)
    recent_latest_date = rss_extract.latest_feed_date(recent_items_xml)
    condensed_date = rss_extract.shift_date_key(condensed_latest_date, days_ago)
    recent_date = rss_extract.shift_date_key(recent_latest_date, days_ago)
    condensed_today_xml = rss_extract.filter_items_by_date(condensed_items_xml, condensed_date)
    recent_today_xml = rss_extract.filter_items_by_date(recent_items_xml, recent_date)[: max(1, recent_n)]

    condensed_items = []
    seen = set()
    for item_xml in condensed_today_xml:
        stub = rss_extract.build_item_stub(item_xml)
        key = raw_item_key(stub)
        if not key or key in seen:
            continue
        seen.add(key)
        stub["feed_group"] = "condensed"
        condensed_items.append(stub)

    recent_items = []
    for item_xml in recent_today_xml:
        stub = rss_extract.build_item_stub(item_xml)
        key = raw_item_key(stub)
        if not key or key in seen:
            continue
        seen.add(key)
        stub["feed_group"] = "recent"
        recent_items.append(stub)

    combined = condensed_items + recent_items
    out_items = []
    for idx, stub in enumerate(combined):
        enriched = rss_extract.enrich_item_payload(stub)
        enriched["abstract_lookup_skipped"] = False
        out_items.append(enriched)
        if idx + 1 < len(combined):
            time.sleep(0.15)

    return {
        "date": shanghai_today(),
        "feed_date_condensed": condensed_date,
        "feed_date_recent": recent_date,
        "source": "APS PRL condensed-matter pool + PRL recent补位",
        "feed_url": f"{condensed_url} | {recent_url}",
        "feed_url_condensed": condensed_url,
        "feed_url_recent": recent_url,
        "days_ago": max(0, int(days_ago)),
        "target_date_condensed": condensed_date,
        "target_date_recent": recent_date,
        "latest_date_condensed": condensed_latest_date,
        "latest_date_recent": recent_latest_date,
        "item_count_in_condensed_feed": len(condensed_items_xml),
        "item_count_in_recent_feed": len(recent_items_xml),
        "item_count_today_condensed": len(condensed_items),
        "item_count_today_recent": len(recent_items),
        "items": out_items,
    }


def issue_meta_from_raw(raw: dict) -> dict:
    days_ago = max(0, int(raw.get("days_ago", 0) or 0))
    if days_ago >= 7:
        return {
            "issue_mode": "weekly",
            "video_title": "PRL本周凝聚态热点",
            "cover_title": "PRL本周凝聚态热点",
            "cover_subtitle": f'{raw.get("date", "")} · 本周凝聚态热点',
            "section_label": "本期精讲",
        }
    return {
        "issue_mode": "daily",
        "video_title": "PRL今日热点",
        "cover_title": "PRL今日热点",
        "cover_subtitle": f'{raw.get("date", "")} · 今日热点',
        "section_label": "今日精讲",
    }


def build_publish_desc(data: dict, raw: dict, *, max_titles: int = 5) -> str:
    del data, max_titles
    parts = []
    feed_date_condensed = (raw.get("feed_date_condensed") or raw.get("target_date_condensed") or "").strip()
    feed_date_recent = (raw.get("feed_date_recent") or raw.get("target_date_recent") or "").strip()
    model = (os.environ.get("OPENAI_MODEL") or "").strip()

    parts.append("数据来源：APS PRL RSS")
    if feed_date_condensed and feed_date_recent and feed_date_condensed != feed_date_recent:
        tail = f"论文日期：{feed_date_condensed}/{feed_date_recent}"
    elif feed_date_condensed or feed_date_recent:
        tail = f"论文日期：{feed_date_condensed or feed_date_recent}"
    else:
        tail = ""
    if model:
        tail = f"{tail}｜模型：{model}" if tail else f"模型：{model}"
    if tail:
        parts.append(tail)
    return "\n".join(parts).strip() + ("\n" if parts else "")


PUBLISH_TAG_RULES = [
    (r"non[- ]?hermitian|exceptional point", "异常点"),
    (r"topolog", "拓扑"),
    (r"moir[eé]", "莫尔"),
    (r"exciton", "激子"),
    (r"ferroelectric", "铁电"),
    (r"anomalous hall", "反常霍尔"),
    (r"quantum hall", "量子霍尔"),
    (r"superconductor", "超导"),
    (r"chirality|chiral|enantiomer", "手性"),
    (r"floquet", "Floquet"),
    (r"landau", "兰道"),
    (r"orbital magnetization", "轨道磁化"),
    (r"twisted bilayer|heterobilayer", "扭转双层"),
    (r"antiferromagnet", "反铁磁"),
]
PUBLISH_TAG_DEFAULTS = ["拓扑", "莫尔", "激子", "量子霍尔", "超导", "手性"]


def normalize_tag_text(text: str) -> str:
    s = normalize_mixed_spacing(text)
    s = re.sub(r"^[#＃]+", "", s)
    s = re.sub(r"[，,、/｜|；;]+", " ", s)
    s = squeeze_spaces(s)
    s = s.replace(" ", "")
    return s


def build_publish_tag_prompt(briefs: list[str]) -> str:
    joined = "\n".join(f"- {b}" for b in briefs if b)
    return (
        "任务：根据这一期 PRL 稿件的全部一句话 brief，总结用于封面/发布的关键词标签。\n"
        "要求：\n"
        "1. 直接返回一行关键词，用英文逗号分隔。\n"
        "2. 返回 4~12 个中文关键词。\n"
        "3. 每个关键词 2~4 个字，尽量短，不要写成句子。\n"
        "4. 优先提炼这一期反复出现或最核心的研究主题，不要机械罗列每篇论文各一个词。\n"
        "5. 不要输出泛词，如 研究、论文、物理、结果、方法、进展、系统。\n"
        "6. 保留必要英文或符号，如 Moiré、Lyman-α、Transmon、Fe(Te,Se)。\n"
        "7. 不要重复、不要同义改写。\n"
        "8. 除这一行关键词外，不要补充解释或前后缀。\n"
        "全部 briefs：\n"
        f"{joined}"
    )


def build_publish_tags(data: dict, *, limit: int = 12) -> str:
    papers = data.get("papers") or []
    briefs = [normalize_mixed_spacing((paper.get("brief") or "").strip()) for paper in papers]
    briefs = [b for b in briefs if b]
    tags: list[str] = []

    if briefs:
        try:
            raw_output = strip_code_fences(call_openai_compatible(build_publish_tag_prompt(briefs))).strip()
            raw_items = []
            if raw_output.startswith("{") or raw_output.startswith("["):
                try:
                    parsed = json.loads(raw_output)
                except Exception:
                    parsed = None
                raw_tags = None
                if isinstance(parsed, dict):
                    raw_tags = parsed.get("tags") or parsed.get("keywords") or parsed.get("keyword")
                elif isinstance(parsed, list):
                    raw_tags = parsed
                if isinstance(raw_tags, str):
                    raw_items = [x.strip() for x in raw_tags.split(",") if x.strip()]
                elif isinstance(raw_tags, list):
                    raw_items = [str(x or "").strip() for x in raw_tags if str(x or "").strip()]
            if not raw_items:
                raw_items = [x.strip() for x in raw_output.replace("\n", ",").split(",") if x.strip()]
            for item in raw_items:
                tag = normalize_tag_text(item)
                if not tag:
                    continue
                if tag in tags:
                    continue
                tags.append(tag)
            if len(tags) >= 4:
                tags = tags[: min(12, limit)]
        except Exception:
            tags = []

    if len(tags) < 4:
        texts = []
        for paper in papers[:8]:
            texts.append((paper.get("title_zh") or "").strip())
            texts.append((paper.get("title_en") or "").strip())
            texts.append((paper.get("brief") or "").strip())
        blob = "\n".join([t for t in texts if t]).lower()
        for pattern, label in PUBLISH_TAG_RULES:
            if re.search(pattern, blob, flags=re.I) and label not in tags:
                tags.append(label)
        for label in PUBLISH_TAG_DEFAULTS:
            if label not in tags:
                tags.append(label)
        tags = tags[: min(12, limit)]

    tags = tags[: min(12, limit)]
    return ",".join(tags) + ("\n" if tags else "")


def short_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:6]


def normalize_formula_text(text: str) -> str:
    s = (text or "").strip()
    s = s.replace("\\mathrm", "")
    s = s.replace("$", "")
    s = re.sub(r"_\{([^}]*)\}", lambda m: (m.group(1) or ""), s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def squeeze_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def normalize_mixed_spacing(text: str) -> str:
    s = squeeze_spaces(normalize_formula_text(text))
    s = re.sub(r"([\u4e00-\u9fff])([A-Za-z0-9])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z0-9])([α-ωΑ-Ω])", r"\1 \2", s)
    s = re.sub(r"([α-ωΑ-Ω])([A-Za-z0-9])", r"\1 \2", s)
    s = re.sub(r"\s+([,，。；：、！？.!?:;])", r"\1", s)
    s = re.sub(r"([(（【《“])\s+", r"\1", s)
    s = re.sub(r"\s+([)）】》”])", r"\1", s)
    return squeeze_spaces(s)


def clean_title_zh(title_en: str) -> str:
    title = normalize_formula_text(title_en)
    replacements = [
        ("Measurement-Based Quantum Computation", "测量式量子计算"),
        ("Measurement-Induced", "测量诱导"),
        ("Conformal Field Theory", "共形场论"),
        ("Interlayer-Exciton", "层间激子"),
        ("Moiré-Resolved Spectroscopy", "莫尔分辨光谱"),
        ("Ultrafast", "超快"),
        ("Thermalization", "热化"),
        ("Heterobilayers", "异质双层"),
        ("Taming", "抑制"),
        ("Rydberg Decay", "里德伯衰变"),
        ("Radiating Black Holes", "辐射黑洞"),
        ("General Relativity", "广义相对论"),
        ("Need Not Be Singular", "未必奇异"),
        ("Elastic Response", "弹性响应"),
        ("Instabilities", "失稳"),
        ("Anomalous Hall Crystals", "反常霍尔晶体"),
        ("Entanglement", "纠缠"),
        ("Twisted", "扭转"),
        ("of", ""),
        ("in", ""),
        ("with", ""),
    ]
    for src, dst in replacements:
        title = title.replace(src, dst)
    title = squeeze_spaces(title.replace(" / ", "/").replace(" - ", "-").replace(" ,", ","))
    return title if any("\u4e00" <= ch <= "\u9fff" for ch in title) else normalize_formula_text(title_en)


def build_fake_content(title_en: str, abstract_en: str) -> tuple[str, str, list[str], list[str]]:
    title_clean = normalize_formula_text(title_en)
    t = title_clean.lower()

    if "measurement-induced" in t and "conformal field theory" in t:
        return (
            "共形场论中的测量诱导纠缠",
            "聚焦共形场论中的测量诱导纠缠，讨论局域测量如何改变多体纠缠结构。",
            [
                "长程纠缠量子临界态中的多体纠缠分布，是这里的核心对象。",
                "局域测量被视为改变纠缠结构的关键扰动。",
                "测量过程如何重塑原本的量子关联模式，是文章想回答的问题。",
            ],
            [
                "测量效应会显著改变多体纠缠的空间结构与演化方式。",
                "把测量问题与量子临界系统中的纠缠动力学直接联系起来。",
                "为理解受测量影响的量子多体系统提供了更清晰的理论线索。",
            ],
        )

    if "rydberg" in t and "measurement-based quantum computation" in t:
        return (
            "测量式量子计算如何抑制里德伯衰变",
            "讨论在测量式量子计算框架下，如何压低里德伯衰变带来的泄漏误差。",
            [
                "可编程中性原子阵列中的里德伯态衰变与门误差，是这里的核心问题。",
                "测量式量子计算被当作处理泄漏与损失问题的切入点。",
                "目标是减少两比特门过程中错误传播对计算稳定性的影响。",
            ],
            [
                "这一路线有机会把里德伯衰变造成的泄漏误差压下来。",
                "如果方案可行，这会提升中性原子平台走向容错量子计算的可靠性。",
                "把具体物理误差与可扩展量子计算直接连接起来。",
            ],
        )

    if "black holes" in t and "singular" in t:
        return (
            "辐射黑洞未必必然奇异",
            "讨论辐射黑洞在广义相对论框架下是否一定走向奇异结构。",
            [
                "重新审视黑洞内部必然出现奇点或柯西视界的常见看法。",
                "场景聚焦在由塌缩形成并持续辐射的带电球对称黑洞。",
                "广义相对论本身是否允许一种非奇异的内部演化路径，是这里的核心问题。",
            ],
            [
                "在特定条件下，辐射黑洞未必必须通向传统意义上的奇异内部。",
                "如果结论成立，人们对黑洞内部结构的经典理解会被改写。",
                "把黑洞奇异性问题重新推回到广义相对论内部讨论。",
            ],
        )

    if "anomalous hall crystals" in t:
        return (
            "反常霍尔晶体的弹性响应与失稳",
            "研究反常霍尔晶体的弹性响应，并讨论这一相中可能出现的失稳机制。",
            [
                "同时打破平移对称并具有量子反常霍尔效应的反常霍尔晶体，是这里的研究对象。",
                "外部形变与晶体内部电子拓扑响应之间的耦合，是分析重点。",
                "哪些弹性模式可能触发结构或相态失稳，是想识别的关键问题。",
            ],
            [
                "这种新奇物态不仅有拓扑性质，也有值得单独研究的力学响应。",
                "为理解反常霍尔晶体的稳定性和失稳路径提供了理论线索。",
                "也为相关多层石墨烯体系中的实验现象提供了新的解释方向。",
            ],
        )

    if "interlayer exciton" in t or "moiré" in t:
        return (
            "扭转 WSe2/WS2 异质双层中层间激子的超快热化",
            "利用超快莫尔分辨光谱，研究扭转 WSe2/WS2 异质双层中的层间激子热化过程。",
            [
                "莫尔异质双层中层间激子的能量弛豫与热化动力学，是这里的研究对象。",
                "使用超快莫尔分辨光谱来追踪不同局域环境下的激子演化。",
                "莫尔势景观如何影响层间激子的热化路径，是这里的关键问题。",
            ],
            [
                "莫尔势景观会显著影响层间激子的热化与能量弛豫过程。",
                "把莫尔超晶格中的激子动力学与超快实验直接联系起来。",
                "为理解扭转范德华异质结构中的能量弛豫过程提供了实验线索。",
            ],
        )

    title_zh = clean_title_zh(title_en)
    abstract_hint = squeeze_spaces(abstract_en)
    first_sentence = abstract_hint.split(". ")[0].strip(" .") if abstract_hint else ""
    second_sentence = ""
    if ". " in abstract_hint:
        second_sentence = abstract_hint.split(". ", 1)[1].split(". ")[0].strip(" .")

    brief = normalize_mixed_spacing(
        first_sentence[:72] if first_sentence else f"{title_zh}给出了这项研究最核心的结果与机制线索。"
    )
    method_results = [
        normalize_mixed_spacing(f"研究对象：{title_zh}。"),
        normalize_mixed_spacing(second_sentence[:88] if second_sentence else "摘要给出了研究采用的方法、关键设置或主要观测线索。"),
        normalize_mixed_spacing("下方内容继续展开结果成立的条件、机制与直接观测。"),
    ]
    summary = [
        normalize_mixed_spacing("核心结论优先落在论文实际给出的结果、机制或适用范围。"),
        normalize_mixed_spacing("如需定量细节与完整边界条件，仍需结合全文核对。"),
    ]
    return title_zh, brief, method_results, summary


def condensed_matter_score(title_en: str, abstract_en: str) -> int:
    text = f"{title_en} {abstract_en}".lower()
    score = 0
    weighted_keywords = {
        "topological": 4,
        "superconductor": 4,
        "superconduct": 4,
        "quantum hall": 4,
        "hall": 3,
        "landau": 3,
        "andreev": 3,
        "moiré": 4,
        "moire": 4,
        "exciton": 4,
        "crystal": 3,
        "ferroelectric": 4,
        "wurtzite": 3,
        "electron gas": 3,
        "spin": 2,
        "lattice": 3,
        "phonon": 3,
        "magnet": 3,
        "condensed matter": 5,
        "non-hermitian": 3,
        "exceptional point": 3,
        "nanostructure": 3,
    }
    for kw, w in weighted_keywords.items():
        if kw in text:
            score += w
    penalties = {
        "supernova": 5,
        "axion": 5,
        "black hole": 6,
        "cosmology": 5,
        "qcd": 4,
        "particle": 2,
        "nuclear": 3,
        "high-energy": 4,
    }
    for kw, w in penalties.items():
        if kw in text:
            score -= w
    return score


def rank_items_by_condensed_matter(items: list[dict]) -> list[dict]:
    indexed = []
    for idx, it in enumerate(items):
        indexed.append((condensed_matter_score(it.get("title_en", ""), it.get("abstract_en", "")), idx, it))
    ranked = sorted(indexed, key=lambda x: (x[0], -x[1]), reverse=True)
    return [it for _, _, it in ranked]


def has_usable_abstract(item: dict) -> bool:
    return bool((item.get("abstract_en") or "").strip())


def split_selected_and_other(items: list[dict], selected_n: int, other_n: int = 10) -> tuple[list[dict], list[dict]]:
    selected_count = max(1, selected_n)
    other_count = max(0, other_n)
    selected = []
    selected_keys = set()
    for it in items:
        if len(selected) >= selected_count:
            break
        key = raw_item_key(it)
        if key and key in selected_keys:
            continue
        if key:
            selected_keys.add(key)
        selected.append(it)

    others = []
    for it in items:
        key = raw_item_key(it)
        if key and key in selected_keys:
            continue
        others.append(it)
        if len(others) >= other_count:
            break
    return selected, others


def fake_fill_from_raw(raw: dict, selected_n: int, other_n: int = 10) -> dict:
    items = raw.get("items", [])
    selected_items, other_items = split_selected_and_other(items, selected_n, other_n)

    papers = []
    for it in selected_items:
        title_en = normalize_formula_text((it.get("title_en") or "").strip())
        doi = (it.get("doi") or "").strip()
        abstract_en = (it.get("abstract_en") or "").strip()
        title_zh, brief, method_results, summary = build_fake_content(title_en, abstract_en)
        brief = normalize_mixed_spacing(brief)
        method_results = [normalize_mixed_spacing(x) for x in method_results]
        summary = [normalize_mixed_spacing(x) for x in summary]
        key_points = [x for x in (method_results + summary) if x]
        papers.append(
            {
                "title_en": title_en,
                "title_zh": title_zh,
                "doi": doi,
                "authors": list(it.get("authors") or []),
                "first_author": (it.get("first_author") or "").strip(),
                "author_text": (it.get("author_text") or "").strip(),
                "brief": brief,
                "key_points": key_points,
                "method_results": method_results,
                "summary": summary,
                "voice_intro": brief,
                "voice_points": [x for x in key_points[:2] if x != brief],
            }
        )

    other_papers = []
    for it in other_items:
        title_en = normalize_formula_text((it.get("title_en") or "").strip())
        other_papers.append(
            {
                "title_en": title_en,
                "title_zh": "",
                "doi": (it.get("doi") or "").strip(),
            }
        )

    return {"date": raw.get("date"), **issue_meta_from_raw(raw), "papers": papers, "other_papers": other_papers}


def call_openai_compatible(prompt: str, *, system_prompt: str = "") -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    if not api_key or not base_url:
        raise RuntimeError("OPENAI_API_KEY or OPENAI_BASE_URL missing")

    url = base_url.rstrip("/") + "/chat/completions"
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


BAD_COPY_PATTERNS = [
    r"围绕.+整理研究问题",
    r"从摘要片段看",
    r"值得继续跟进",
    r"进一步核对",
    r"重点在于",
    r"核心目标是",
    r"如果方案可行",
    r"如果结论成立",
    r"论文",
    r"文章",
    r"本研究",
    r"重点如下",
    r"直接结论是",
    r"^我们",
    r"^作者",
    r"^本文",
    r"^这篇",
]


GENERATED_PREFIX_PATTERNS = [
    r"^研究对象是[:：，, ]*",
    r"^研究对象[:：，, ]*",
    r"^这项工作表明[:：，, ]*",
    r"^这项工作说明[:：，, ]*",
    r"^这项工作[:：，, ]*",
    r"^该工作表明[:：，, ]*",
    r"^该工作说明[:：，, ]*",
    r"^该工作[:：，, ]*",
    r"^主要结论是[:：，, ]*",
    r"^我们发现[:：，, ]*",
    r"^我们首次[:：，, ]*",
    r"^我们[:：，, ]*",
    r"^作者发现[:：，, ]*",
    r"^作者提出[:：，, ]*",
    r"^作者建立了[:：，, ]*",
    r"^作者建立[:：，, ]*",
    r"^作者[:：，, ]*",
    r"^本文[:：，, ]*",
]


def strip_generated_prefixes(text: str) -> str:
    s = normalize_mixed_spacing(text)
    for pat in GENERATED_PREFIX_PATTERNS:
        s = re.sub(pat, "", s)
    return normalize_mixed_spacing(s)


def looks_bad_generated_text(text: str) -> bool:
    s = strip_generated_prefixes(text)
    if not s:
        return True
    for pat in BAD_COPY_PATTERNS:
        if re.search(pat, s):
            return True
    return False


def clean_text_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for x in value:
        s = strip_generated_prefixes(str(x or ""))
        if s:
            out.append(s)
    return out


def salvage_text_lines(text: str) -> list[str]:
    s = strip_code_fences(text or "").strip()
    if not s:
        return []

    parts = []
    for line in re.split(r"[\r\n]+", s):
        line = re.sub(r"^\s*[-*•·\d]+[.)、．]?\s*", "", line).strip()
        if line:
            parts.append(line)
    cleaned = clean_text_list(parts)
    if len(cleaned) >= 3:
        return cleaned[:6]

    blob = re.sub(r"\s+", " ", s)
    blob = re.sub(r"^\s*[-*•·]+\s*", "", blob)
    pieces = re.split(r"(?<=[。！？；;])\s*", blob)
    pieces = [strip_generated_prefixes(p.strip()) for p in pieces if p.strip()]
    pieces = [p for p in pieces if p]
    return pieces[:6]


def current_api_debug_log_path() -> Path | None:
    raw = os.environ.get("PRL_API_DEBUG_LOG", "").strip()
    if not raw:
        return None
    return Path(raw)


def preview_value(value, limit: int = 300) -> str:
    try:
        if isinstance(value, str):
            text = value
        else:
            text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = repr(value)
    text = squeeze_spaces(text)
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def log_api_event(*, paper_title_en: str, doi: str, stage: str, attempt: int, status: str, error_type: str = "", validator_reason: str = "", raw_preview: str = "", parsed_preview: str = "") -> None:
    path = current_api_debug_log_path()
    if path is None:
        return
    log_runtime_event(
        path,
        source="llm_api",
        event=stage,
        status=status,
        paper_title_en=paper_title_en,
        doi=doi,
        stage=stage,
        attempt=attempt,
        error_type=error_type,
        validator_reason=validator_reason,
        raw_preview=raw_preview,
        parsed_preview=parsed_preview,
    )


def explain_page_payload_failure(data) -> str:
    if not isinstance(data, dict):
        return "page:not_dict"
    raw_points = data.get("key_points")
    if not isinstance(raw_points, list):
        return "page:key_points_not_list"
    cleaned = clean_text_list(raw_points)
    if len(cleaned) < 3:
        return f"page:key_points_too_short:{len(cleaned)}"
    for idx, point in enumerate(cleaned, 1):
        if looks_bad_generated_text(point):
            return f"page:key_point_bad_copy:{idx}"
    return "page:unknown"


def explain_voice_payload_failure(data) -> str:
    if isinstance(data, str):
        intro = strip_generated_prefixes(data.strip())
    elif isinstance(data, dict):
        intro = strip_generated_prefixes(
            str(
                data.get("brief")
                or data.get("voice_intro")
                or data.get("intro")
                or data.get("sentence")
                or data.get("text")
                or ""
            )
        )
    else:
        return "voice:unsupported_type"
    if not intro:
        return "voice:empty_intro"
    if looks_bad_generated_text(intro):
        return "voice:bad_copy"
    return "voice:unknown"


def explain_title_payload_failure(data) -> str:
    if isinstance(data, str):
        title_zh = squeeze_spaces(normalize_formula_text(data))
    elif isinstance(data, dict):
        title_zh = squeeze_spaces(
            normalize_formula_text(
                str(
                    data.get("title_zh")
                    or data.get("title")
                    or data.get("translation")
                    or data.get("text")
                    or ""
                )
            )
        )
    else:
        return "title:unsupported_type"
    if not title_zh:
        return "title:empty"
    if looks_bad_generated_text(title_zh):
        return "title:bad_copy"
    if not re.search(r"[\u4e00-\u9fff]", title_zh):
        return "title:no_chinese"
    return "title:unknown"


def explain_validator_failure(stage: str, parsed) -> str:
    if stage == "page":
        return explain_page_payload_failure(parsed)
    if stage == "voice":
        return explain_voice_payload_failure(parsed)
    if stage == "title":
        return explain_title_payload_failure(parsed)
    return f"{stage}:validation_failed"


def validate_page_payload(data: dict | list | str) -> dict | None:
    if isinstance(data, str):
        text = data.strip()
        if not text:
            return None
        reparsed = None
        if text.startswith("{") or text.startswith("["):
            try:
                reparsed = json.loads(text)
            except json.JSONDecodeError:
                reparsed = None
        if isinstance(reparsed, (dict, list)):
            data = reparsed
        else:
            lines = salvage_text_lines(text)
            if len(lines) >= 3:
                return {"key_points": lines[:6]}
            return None
    if isinstance(data, list):
        key_points = clean_text_list(data)
    else:
        raw_points = data.get("key_points") or data.get("points") or data.get("bullets") or data.get("items")
        key_points = clean_text_list(raw_points)
    if len(key_points) < 3:
        return None
    return {
        "key_points": key_points[:6],
    }


def validate_voice_payload(data: dict | str) -> dict | None:
    if isinstance(data, str):
        intro = strip_generated_prefixes(data.strip())
    else:
        intro = strip_generated_prefixes(
            str(
                data.get("brief")
                or data.get("voice_intro")
                or data.get("intro")
                or data.get("sentence")
                or data.get("text")
                or ""
            )
        )
    if not intro:
        return None
    return {
        "title_zh": "",
        "voice_intro": intro,
        "voice_points": [],
    }


def validate_title_payload(data: dict | str) -> dict | None:
    if isinstance(data, str):
        title_zh = squeeze_spaces(normalize_formula_text(data))
    else:
        title_zh = squeeze_spaces(
            normalize_formula_text(
                str(
                    data.get("title_zh")
                    or data.get("title_cn")
                    or data.get("title")
                    or data.get("translation")
                    or data.get("text")
                    or ""
                )
            )
        )
    if not title_zh:
        return None
    if not re.search(r"[\u4e00-\u9fff]", title_zh):
        return None
    return {"title_zh": title_zh}


def detect_quality_warning(stage: str, validated: dict) -> str:
    if stage == "page":
        for idx, point in enumerate(validated.get("key_points") or [], 1):
            if looks_bad_generated_text(point):
                return f"page:key_point_bad_copy:{idx}"
        return ""
    if stage == "voice":
        intro = str(validated.get("voice_intro") or "")
        if looks_bad_generated_text(intro):
            return "voice:bad_copy"
        return ""
    if stage == "title":
        title_zh = str(validated.get("title_zh") or "")
        if looks_bad_generated_text(title_zh):
            return "title:bad_copy"
        return ""
    return ""


def request_json_with_retry(prompt: str, validator, *, label: str, paper_title_en: str, doi: str) -> dict | None:
    stage = label.split(":", 1)[0].strip() or "unknown"
    for attempt in range(1, 3):
        log_api_event(
            paper_title_en=paper_title_en,
            doi=doi,
            stage=stage,
            attempt=attempt,
            status="request_started",
        )
        try:
            raw_output = strip_code_fences(
                call_openai_compatible(prompt, system_prompt="只输出 JSON，不要输出 markdown 或解释。")
            ).strip()
            try:
                parsed = json.loads(raw_output)
                parsed_from_json = True
            except json.JSONDecodeError:
                parsed = raw_output
                parsed_from_json = False
        except (urllib.error.URLError, TimeoutError):
            log_api_event(
                paper_title_en=paper_title_en,
                doi=doi,
                stage=stage,
                attempt=attempt,
                status="network_error",
                error_type="URLError_or_TimeoutError",
            )
            if attempt < 2:
                time.sleep(10)
                continue
            return None
        except (RuntimeError, KeyError, ValueError) as e:
            log_api_event(
                paper_title_en=paper_title_en,
                doi=doi,
                stage=stage,
                attempt=attempt,
                status="runtime_error",
                error_type=type(e).__name__,
            )
            if attempt < 2:
                continue
            return None

        validated = validator(parsed)
        if validated is not None:
            log_api_event(
                paper_title_en=paper_title_en,
                doi=doi,
                stage=stage,
                attempt=attempt,
                status="success",
                error_type="json" if parsed_from_json else "raw_text",
                raw_preview=preview_value(raw_output),
                parsed_preview=preview_value(validated),
            )
            warning_reason = detect_quality_warning(stage, validated)
            if warning_reason:
                log_api_event(
                    paper_title_en=paper_title_en,
                    doi=doi,
                    stage=stage,
                    attempt=attempt,
                    status="warning",
                    error_type="json" if parsed_from_json else "raw_text",
                    validator_reason=warning_reason,
                    raw_preview=preview_value(raw_output),
                    parsed_preview=preview_value(validated),
                )
            return validated
        log_api_event(
            paper_title_en=paper_title_en,
            doi=doi,
            stage=stage,
            attempt=attempt,
            status="validation_failed",
            error_type="json" if parsed_from_json else "raw_text",
            validator_reason=explain_validator_failure(stage, parsed),
            raw_preview=preview_value(raw_output),
            parsed_preview=preview_value(parsed),
        )
        if attempt < 2:
            continue
    return None


def request_text_with_retry(prompt: str, validator, *, label: str, paper_title_en: str, doi: str):
    stage = label.split(":", 1)[0].strip() or "unknown"
    for attempt in range(1, 3):
        log_api_event(
            paper_title_en=paper_title_en,
            doi=doi,
            stage=stage,
            attempt=attempt,
            status="request_started",
        )
        try:
            raw_output = strip_code_fences(call_openai_compatible(prompt)).strip()
        except (urllib.error.URLError, TimeoutError):
            log_api_event(
                paper_title_en=paper_title_en,
                doi=doi,
                stage=stage,
                attempt=attempt,
                status="network_error",
                error_type="URLError_or_TimeoutError",
            )
            if attempt < 2:
                time.sleep(10)
                continue
            return None
        except (RuntimeError, KeyError, ValueError) as e:
            log_api_event(
                paper_title_en=paper_title_en,
                doi=doi,
                stage=stage,
                attempt=attempt,
                status="runtime_error",
                error_type=type(e).__name__,
            )
            if attempt < 2:
                continue
            return None

        validated = validator(raw_output)
        if validated is not None:
            log_api_event(
                paper_title_en=paper_title_en,
                doi=doi,
                stage=stage,
                attempt=attempt,
                status="success",
                error_type="raw_text",
                raw_preview=preview_value(raw_output),
                parsed_preview=preview_value(validated),
            )
            return validated
        log_api_event(
            paper_title_en=paper_title_en,
            doi=doi,
            stage=stage,
            attempt=attempt,
            status="validation_failed",
            error_type="raw_text",
            validator_reason="text_validator_rejected",
            raw_preview=preview_value(raw_output),
            parsed_preview=preview_value(raw_output),
        )
        if attempt < 2:
            continue
    return None


def build_page_copy_prompt(item: dict) -> str:
    title_en = normalize_formula_text((item.get("title_en") or "").strip())
    abstract_en = squeeze_spaces((item.get("abstract_en") or "").strip())
    return (
        "任务：为 PRL 单篇精读页生成关键要点文案。\n"
        "输出格式要求（必须严格遵守）：\n"
        "1. 只返回纯文本，不要返回 JSON、列表、Markdown、代码块或任何字段名。\n"
        "2. 总共返回 4~6 行，每行正好 1 句中文。\n"
        "3. 每行都必须以中文句号‘。’结尾。\n"
        "4. 不要写序号、项目符号、引号、括号说明、前言、结语或任何额外内容。\n"
        "5. 不要出现 points、key_points、bullets、items 等字样。\n"
        "6. 如果拿不准，也只返回纯文本句子，不要包装成任何结构。\n"
        "内容要求：\n"
        "7. 这些句子合在一起，应自然覆盖这篇工作的研究对象、采用的方法或关键设定、直接结果，以及最值得记住的物理含义、适用范围或限制。\n"
        "8. 不要机械区分方法和结论，只按自然叙述顺序组织内容。\n"
        "9. 每句尽量只表达一个清晰信息点，避免一句话里塞太多层次。\n"
        "10. 直接写具体内容，不要写成提纲腔、导读腔或总结腔。\n"
        "11. 只用自然中文表达；能直接写清楚就直接写清楚，不要故意夹英文短语。\n"
        "12. 化学式保持原写法，不要改写成中文名称。\n"
        "13. 遇到化学式、特殊符号、变量名、群记号或公式时，统一用行内 LaTeX 形式写成 $...$，不要输出 Unicode 数学花体字母、上标下标异体字或其他花哨符号。\n"
        "14. 例如：把 𝓕 写成 $\\mathscr{F}$，把 L_𝓕U(1) 写成 $L_{\\mathscr{F}}U(1)$，把 f(k) 写成 $f(k)$。\n"
        "15. 只根据标题和摘要写，不补充摘要里没有的信息。\n"
        "16. 宁可少写一点，也不要为了凑条数写空话、套话或重复句。\n"
        "禁止出现的开头或套话：这项工作、该工作、本文、作者、研究对象是、重点考察、核心在于、结论是、其意义在于、结果表明、进一步表明、值得注意的是、可以看出。\n"
        "补充要求：每句都要能单独读通，不能是残句、半句或从句；不要先写空泛判断，再补内容；如果摘要本身没有给出意义或限制，可以不单独硬写这一类句子。\n"
        f"title_en: {json.dumps(title_en, ensure_ascii=False)}\n"
        f"abstract_en: {json.dumps(abstract_en, ensure_ascii=False)}"
    )


def build_voice_copy_prompt(item: dict) -> str:
    title_en = normalize_formula_text((item.get("title_en") or "").strip())
    abstract_en = squeeze_spaces((item.get("abstract_en") or "").strip())
    return (
        "任务：用自然中文写一句简短介绍，用作这篇 PRL 论文的语音开场。\n\n"
        "要求：\n"
        "1. 只返回一句话，不要分点，不要解释，不要输出其他内容。\n"
        "2. 这句话要适合口播，像是在开头介绍这篇论文。\n"
        "3. 只写这篇工作的核心发现、关键结果或最重要的机制线索，尽量短。\n"
        "4. 不要刻意夹英文短语，不要翻译标题，不要补充摘要里没有的信息。\n\n"
        f"title_en: {json.dumps(title_en, ensure_ascii=False)}\n"
        f"abstract_en: {json.dumps(abstract_en, ensure_ascii=False)}"
    )


def build_title_translation_prompt(item: dict) -> str:
    title_en = normalize_formula_text((item.get("title_en") or "").strip())
    abstract_en = squeeze_spaces((item.get("abstract_en") or "").strip())
    return (
        "任务：把这篇 PRL 论文标题翻成自然中文。\n"
        "要求：\n"
        "1. 只返回中文标题这一行。\n"
        "2. 只翻译标题本身，不要补充解释，不要输出别的内容。\n"
        "3. 要像中文论文标题，简洁、自然、准确。\n"
        "4. 化学式保持原写法，不要改写成中文名称。\n"
        "5. 普通物理术语尽量译成中文；不要保留整句英文。\n"
        "6. 只根据标题和摘要判断语义，不补充摘要里没有的信息。\n"
        f"title_en: {json.dumps(title_en, ensure_ascii=False)}\n"
        f"abstract_en: {json.dumps(abstract_en, ensure_ascii=False)}"
    )


def api_fill_from_raw(raw: dict, selected_n: int, other_n: int = 10) -> dict:
    selected_items, other_items = split_selected_and_other(raw.get("items", []), selected_n, other_n)
    papers = []

    for it in selected_items:
        if len(papers) >= max(1, selected_n):
            break
        title_en = normalize_formula_text((it.get("title_en") or "").strip())
        doi = (it.get("doi") or "").strip()
        if not title_en:
            continue

        page_payload = request_json_with_retry(
            build_page_copy_prompt(it),
            validate_page_payload,
            label=f"page:{title_en}",
            paper_title_en=title_en,
            doi=doi,
        )
        if not page_payload:
            continue

        voice_payload = request_json_with_retry(
            build_voice_copy_prompt(it),
            validate_voice_payload,
            label=f"voice:{title_en}",
            paper_title_en=title_en,
            doi=doi,
        )
        if not voice_payload:
            continue

        title_payload = request_json_with_retry(
            build_title_translation_prompt(it),
            validate_title_payload,
            label=f"title:{title_en}",
            paper_title_en=title_en,
            doi=doi,
        )
        if not title_payload:
            continue

        papers.append(
            {
                "title_en": title_en,
                "title_zh": title_payload["title_zh"],
                "doi": doi,
                "authors": list(it.get("authors") or []),
                "first_author": (it.get("first_author") or "").strip(),
                "author_text": (it.get("author_text") or "").strip(),
                "brief": voice_payload["voice_intro"],
                "key_points": page_payload["key_points"],
                "voice_intro": voice_payload["voice_intro"],
                "voice_points": voice_payload["voice_points"],
            }
        )

    if not papers:
        raise RuntimeError("API fill produced 0 valid papers")

    other_papers = []
    for it in other_items[: max(0, other_n)]:
        title_en = normalize_formula_text((it.get("title_en") or "").strip())
        other_papers.append(
            {
                "title_en": title_en,
                "title_zh": "",
                "doi": (it.get("doi") or "").strip(),
            }
        )

    return {"date": raw.get("date"), **issue_meta_from_raw(raw), "papers": papers, "other_papers": other_papers}


def generate_input_json(raw: dict, prompt: str, selected_n: int) -> tuple[dict, str]:
    mode = os.environ.get("PRL_LLM_MODE", "fake").strip().lower()
    if mode not in {"fake", "api", "auto"}:
        mode = "fake"

    if mode in {"api", "auto"}:
        try:
            return api_fill_from_raw(raw, selected_n, other_n=10), "api"
        except (RuntimeError, urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as e:
            if mode == "api":
                raise RuntimeError(f"OpenAI-compatible API fill failed: {e}") from e

    return fake_fill_from_raw(raw, selected_n, other_n=10), "fake"


def build_llm_prompt(raw: dict, selected_n: int) -> str:
    schema = {
        "date": raw["date"],
        "papers": [
            {
                "title_en": "...",
                "title_zh": "...",
                "doi": "...",
                "brief": "...",
                "key_points": ["...", "...", "...", "..."],
                "voice_intro": "...",
                "voice_points": ["...", "..."],
            }
        ],
        "other_papers": [
            {
                "title_en": "...",
                "title_zh": "...",
                "doi": "...",
            }
        ],
    }

    raw_for_llm = {
        "date": raw.get("date"),
        "papers": [
            {
                "rank": idx + 1,
                "feed_group": it.get("feed_group", ""),
                "title_en": it.get("title_en", ""),
                "doi": it.get("doi", ""),
                "abstract_en": it.get("abstract_en", ""),
            }
            for idx, it in enumerate(raw.get("items", []))
        ],
    }

    paper_count = min(max(1, selected_n), len(raw.get("items", [])))
    return (
        "任务：根据给定日期的 PRL 条目，为提供的论文逐篇生成可直接用于视频的精讲内容。\n"
        "目标：\n"
        f"1. papers 按给定顺序保留前 {paper_count} 篇并全部生成精讲内容；如果当天不足 {selected_n} 篇，就按实际篇数全部保留。\n"
        "2. brief 同时就是 voice_intro，用一句话写最核心发现、首次结果、关键机制或新方法。\n"
        "3. key_points 用 4~6 条完整中文句子写研究对象、方法/关键设定、直接结果，以及最值得记住的物理含义、适用范围或限制。\n"
        "4. 不要机械区分“方法”和“结论”，只按自然叙述顺序组织 key_points。\n"
        "5. voice_points 再补 1~2 句最关键的机制、条件或结果，适合直接口播。\n"
        "6. 只根据标题和给定摘要内容写，不要补充外部信息，也不要讨论摘要是否完整。\n"
        "7. 若 other_papers 为空就返回空列表。\n"
        f"RSS 原始数据：{json.dumps(raw_for_llm, ensure_ascii=False)}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--feed-n", type=int, default=25, help="How many recent RSS papers to inspect for same-day补位")
    ap.add_argument("--days-ago", type=int, default=0, help="0=latest day(日报), 7=7天前(周报)；仅改变取稿日期")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--filled", default=None, help="Path to filled input.json; if provided, render video")
    ap.add_argument("--auto-fill", action="store_true", help="Automatically generate input.json (fake mode by default; API optional)")
    ap.add_argument("--render", action="store_true", help="Render out.mp4 after auto-fill")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    raw_path = outdir / "raw.json"
    api_debug_log_path = outdir / "api_debug.jsonl"
    if api_debug_log_path.exists():
        api_debug_log_path.unlink()
    os.environ["PRL_API_DEBUG_LOG"] = str(api_debug_log_path)

    # Step 1: build daily raw from condensed feed + recent feed
    raw = build_daily_raw(selected_n=args.n, recent_n=args.feed_n, days_ago=args.days_ago)
    raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Step 2: print prompt
    prompt_path = outdir / "llm_prompt.txt"
    prompt = build_llm_prompt(raw, args.n)
    prompt_path.write_text(prompt, encoding="utf-8")

    if args.auto_fill:
        generated, mode = generate_input_json(raw, prompt, args.n)
        filled_path = outdir / "input.json"
        filled_path.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        publish_desc_path = outdir / "publish_desc.txt"
        publish_desc_path.write_text(build_publish_desc(generated, raw), encoding="utf-8")
        publish_tags_path = outdir / "publish_tags.txt"
        publish_tags_path.write_text(build_publish_tags(generated), encoding="utf-8")
        if not args.render and not args.filled:
            print(str(filled_path))
            print(f"mode={mode}")
            return
        if not args.filled:
            args.filled = str(filled_path)

    if not args.filled:
        print(str(prompt_path))
        return

    # Step 3: render
    run([sys.executable, str(Path(__file__).with_name("render_prl.py")), "--input", str(Path(args.filled)), "--outdir", str(outdir)])
    print(str(outdir / "out.mp4"))


if __name__ == "__main__":
    main()
