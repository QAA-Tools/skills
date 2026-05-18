import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime_logger import log_runtime_event
import datetime as dt


SYSTEM_JSON_ONLY = "只输出 JSON，不要输出 markdown 或解释。"
POLLUTION_TOKENS = {"points", "key_points", "items", "bullets", "voice_points", "voice_intro"}
GENERATED_PREFIX_PATTERNS = [
    r"^这项工作[:：，, ]*",
    r"^该工作[:：，, ]*",
    r"^本文[:：，, ]*",
    r"^作者[:：，, ]*",
    r"^研究对象是[:：，, ]*",
    r"^结果表明[:：，, ]*",
]


def squeeze_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def normalize_formula_text(text: str) -> str:
    return squeeze_spaces(text)


def strip_code_fences(text: str) -> str:
    s = str(text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    return s


def strip_generated_prefixes(text: str) -> str:
    s = squeeze_spaces(text)
    for pat in GENERATED_PREFIX_PATTERNS:
        s = re.sub(pat, "", s)
    return squeeze_spaces(s)


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


def call_openai_compatible(prompt: str, *, model_name: str, system_prompt: str = "") -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    model = str(model_name or os.environ.get("OPENAI_MODEL", "gpt-5.5"))
    if not api_key or not base_url:
        raise RuntimeError("OPENAI_API_KEY or OPENAI_BASE_URL missing")
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
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


def _clean_sentence(text: str) -> str:
    s = strip_generated_prefixes(strip_code_fences(text))
    s = squeeze_spaces(s)
    return s


def _looks_like_pollution(text: str) -> bool:
    s = squeeze_spaces(text).lower()
    if not s:
        return True
    if s in POLLUTION_TOKENS:
        return True
    if any(token in s for token in ["{", "}", "[", "]"]):
        return True
    return False


def _extract_sentences(text: str) -> list[str]:
    s = strip_code_fences(text).strip()
    if not s:
        return []
    lines = []
    for line in re.split(r"[\r\n]+", s):
        line = re.sub(r"^\s*[-*•·\d]+[.)、．]?\s*", "", line).strip()
        if line:
            lines.append(line)
    if len(lines) >= 2:
        return [_clean_sentence(line) for line in lines if _clean_sentence(line)]
    parts = re.split(r"(?<=[。！？；;])\s*", s)
    return [_clean_sentence(part) for part in parts if _clean_sentence(part)]


def _validate_sentence_list(lines: list[str], *, min_count: int, max_count: int) -> list[str] | None:
    cleaned = []
    for raw in lines:
        s = _clean_sentence(raw)
        if not s or _looks_like_pollution(s):
            return None
        if not s.endswith("。"):
            return None
        cleaned.append(s)
    if not (min_count <= len(cleaned) <= max_count):
        return None
    return cleaned


def validate_stage3_page_payload(data) -> dict | None:
    if isinstance(data, str):
        text = data.strip()
        reparsed = None
        if text.startswith("{") or text.startswith("["):
            try:
                reparsed = json.loads(text)
            except json.JSONDecodeError:
                reparsed = None
        if reparsed is not None:
            data = reparsed
        else:
            lines = _extract_sentences(text)
            cleaned = _validate_sentence_list(lines, min_count=4, max_count=6)
            return {"key_points": cleaned} if cleaned is not None else None
    if isinstance(data, list):
        raw_points = data
    elif isinstance(data, dict):
        raw_points = data.get("key_points") or data.get("points") or data.get("bullets") or data.get("items")
    else:
        return None
    if not isinstance(raw_points, list):
        return None
    cleaned = _validate_sentence_list([str(x or "") for x in raw_points], min_count=4, max_count=6)
    return {"key_points": cleaned} if cleaned is not None else None


def validate_stage3_voice_payload(data) -> dict | None:
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                return None
        else:
            return None
    if not isinstance(data, dict):
        return None
    intro = _clean_sentence(str(data.get("voice_intro") or data.get("intro") or data.get("brief") or ""))
    raw_points = data.get("voice_points") or data.get("points") or []
    if not intro or _looks_like_pollution(intro):
        return None
    if not isinstance(raw_points, list):
        return None
    cleaned = _validate_sentence_list([str(x or "") for x in raw_points], min_count=1, max_count=2)
    if cleaned is None:
        return None
    return {"voice_intro": intro, "voice_points": cleaned}


def validate_stage3_title_payload(data) -> dict | None:
    if isinstance(data, str):
        text = data.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = text
    if isinstance(data, dict):
        title_zh = normalize_formula_text(str(data.get("title_zh") or data.get("title") or data.get("translation") or ""))
    else:
        title_zh = normalize_formula_text(str(data or ""))
    title_zh = squeeze_spaces(title_zh)
    if not title_zh:
        return None
    if "\n" in title_zh or _looks_like_pollution(title_zh):
        return None
    if not re.search(r"[\u4e00-\u9fff]", title_zh):
        return None
    return {"title_zh": title_zh}


def explain_validator_failure(stage: str, parsed) -> str:
    if stage == "page":
        return "page validation failed"
    if stage == "voice":
        return "voice validation failed"
    if stage == "title":
        return "title validation failed"
    return f"{stage} validation failed"


def request_json_with_retry(prompt: str, validator, *, label: str, paper_title_en: str, doi: str, model_name: str) -> dict | None:
    stage = label.split(":", 1)[0].strip() or "unknown"
    for attempt in range(1, 3):
        log_api_event(paper_title_en=paper_title_en, doi=doi, stage=stage, attempt=attempt, status="request_started")
        try:
            raw_output = strip_code_fences(call_openai_compatible(prompt, model_name=model_name, system_prompt=SYSTEM_JSON_ONLY)).strip()
            try:
                parsed = json.loads(raw_output)
                parsed_from_json = True
            except json.JSONDecodeError:
                parsed = raw_output
                parsed_from_json = False
        except (urllib.error.URLError, TimeoutError):
            log_api_event(paper_title_en=paper_title_en, doi=doi, stage=stage, attempt=attempt, status="network_error", error_type="URLError_or_TimeoutError")
            if attempt < 2:
                time.sleep(10)
                continue
            return None
        except (RuntimeError, KeyError, ValueError) as e:
            log_api_event(paper_title_en=paper_title_en, doi=doi, stage=stage, attempt=attempt, status="runtime_error", error_type=type(e).__name__)
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
    return None


def build_stage3_page_prompt(item: dict) -> str:
    title_en = normalize_formula_text(item.get("title_en") or "")
    abstract_en = squeeze_spaces(item.get("abstract_en") or "")
    return (
        "任务：为这篇论文生成单篇精读页的关键要点文案。\n"
        "输出格式要求（必须严格遵守）：\n"
        "1. 只返回纯文本，不要返回 JSON、列表、Markdown、代码块或字段名。\n"
        "2. 总共返回 4~6 行，每行正好 1 句中文。\n"
        "3. 每行都必须以中文句号‘。’结尾。\n"
        "4. 不要写序号、项目符号、引号、括号说明、前言、结语或任何额外内容。\n"
        "5. 不要出现 points、key_points、bullets、items 等字样。\n"
        "6. 只根据标题和摘要写，不补充摘要里没有的信息。\n"
        f"title_en: {json.dumps(title_en, ensure_ascii=False)}\n"
        f"abstract_en: {json.dumps(abstract_en, ensure_ascii=False)}"
    )


def build_stage3_voice_prompt(item: dict) -> str:
    title_en = normalize_formula_text(item.get("title_en") or "")
    abstract_en = squeeze_spaces(item.get("abstract_en") or "")
    return (
        "任务：为这篇论文生成口播开场和补充要点。\n"
        "输出格式要求（必须严格遵守）：\n"
        "1. 返回一个 JSON 对象，包含 voice_intro 和 voice_points 两个字段。\n"
        "2. voice_intro 只写 1 句中文。\n"
        "3. voice_points 写 1~2 句中文，每句单独成项，并且都以中文句号‘。’结尾。\n"
        "4. 不要补充解释，不要输出其他字段。\n"
        "5. 只根据标题和摘要写，不补充摘要里没有的信息。\n"
        f"title_en: {json.dumps(title_en, ensure_ascii=False)}\n"
        f"abstract_en: {json.dumps(abstract_en, ensure_ascii=False)}"
    )


def build_stage3_title_prompt(item: dict) -> str:
    title_en = normalize_formula_text(item.get("title_en") or "")
    abstract_en = squeeze_spaces(item.get("abstract_en") or "")
    return (
        "任务：把这篇论文标题翻成自然中文。\n"
        "要求：\n"
        "1. 只返回中文标题这一行。\n"
        "2. 只翻译标题本身，不要补充解释，不要输出别的内容。\n"
        "3. 要像中文论文标题，简洁、自然、准确。\n"
        "4. 只根据标题和摘要判断语义，不补充摘要里没有的信息。\n"
        f"title_en: {json.dumps(title_en, ensure_ascii=False)}\n"
        f"abstract_en: {json.dumps(abstract_en, ensure_ascii=False)}"
    )


def generate_one_paper(row: dict, *, request_json_fn=request_json_with_retry, model_name: str) -> dict:
    item = {
        "title_en": row.get("title_en") or "",
        "abstract_en": row.get("abstract_en") or "",
        "doi": row.get("doi") or "",
    }
    title_en = item["title_en"]
    doi = item["doi"]
    page_payload = request_json_fn(
        build_stage3_page_prompt(item),
        validate_stage3_page_payload,
        label=f"page:{title_en}",
        paper_title_en=title_en,
        doi=doi,
        model_name=model_name,
    )
    if not page_payload:
        raise RuntimeError("page generation failed")
    voice_payload = request_json_fn(
        build_stage3_voice_prompt(item),
        validate_stage3_voice_payload,
        label=f"voice:{title_en}",
        paper_title_en=title_en,
        doi=doi,
        model_name=model_name,
    )
    if not voice_payload:
        raise RuntimeError("voice generation failed")
    title_payload = request_json_fn(
        build_stage3_title_prompt(item),
        validate_stage3_title_payload,
        label=f"title:{title_en}",
        paper_title_en=title_en,
        doi=doi,
        model_name=model_name,
    )
    if not title_payload:
        raise RuntimeError("title generation failed")
    return {
        "record_id": row.get("record_id") or "",
        "title_en": title_en,
        "title_zh": title_payload["title_zh"],
        "doi": doi,
        "paper_url": row.get("paper_url") or "",
        "brief": voice_payload["voice_intro"],
        "key_points": list(page_payload["key_points"]),
        "voice_intro": voice_payload["voice_intro"],
        "voice_points": list(voice_payload["voice_points"]),
    }


def fake_generate_one_paper(row: dict, *, request_json_fn=None, model_name: str) -> dict:
    title_zh = f"中文标题：{(row.get('title_en') or '').strip()[:20]}".strip("：")
    key_points = [
        "这篇工作围绕给定主题建立了清晰的问题背景。",
        "摘要给出了可识别的方法或关键设定。",
        "结果部分提供了可以直接复述的主要发现。",
        "这些信息足以支撑后续渲染和口播生成。",
    ]
    voice_intro = "这篇论文给出了当前主题下最值得先讲的一条主结论。"
    voice_points = ["第一点聚焦论文最直接的结果。", "第二点补充方法或适用范围。"]
    return {
        "record_id": row.get("record_id") or "",
        "title_en": row.get("title_en") or "",
        "title_zh": title_zh or "中文标题",
        "doi": row.get("doi") or "",
        "paper_url": row.get("paper_url") or "",
        "brief": voice_intro,
        "key_points": key_points,
        "voice_intro": voice_intro,
        "voice_points": voice_points,
    }
