import re

from llm_client import request_validated_text


def build_score_prompt(journal_name: str, score_kind: str) -> str:
    if score_kind == "ai":
        rubric = (
            "请按这个期刊在物理学领域中公认的地位与排序，给出 0 到 1 之间的辅助分。"
            "Science / Nature 正刊按 1.0 参考，重要子刊按 0.95 参考，PRL 按 0.95 参考。"
            "与物理学不相干记为 0.0。"
            "只返回一个数字，保留到小数点后 1 位，不要返回 JSON，不要加单位，不要加解释，不要加前后缀。"
        )
    else:
        rubric = (
            "请给出这个期刊的 Journal Impact Factor 数值。"
            "非正式期刊、预印本平台、数据仓库返回 0。"
            "只返回一个数字，保留到小数点后 1 位，不要返回 JSON，不要加单位，不要加解释，不要加前后缀。"
        )
    return f"期刊名：{journal_name}\n{rubric}"


def parse_numeric_score_response(text: str) -> float:
    raw = (text or "").strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", raw):
        raise ValueError("score response is not a pure number")
    return float(raw)


def validate_numeric_score_text(text: str) -> float | None:
    try:
        return parse_numeric_score_response(text)
    except ValueError:
        return None


def score_journal_once(
    journal_name: str,
    score_kind: str,
    sample_index: int,
    model_name: str,
    *,
    request_text_fn=request_validated_text,
) -> float:
    result = request_text_fn(
        build_score_prompt(journal_name, score_kind),
        validate_numeric_score_text,
        model_name,
        label=f"score_{score_kind}:{journal_name}:{sample_index}",
        paper_title_en=journal_name,
        doi="",
    )
    text = result["text"] if isinstance(result, dict) else result
    return float(text)
