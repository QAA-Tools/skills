import os

from prl_llm_core import request_text_with_retry


def request_validated_text(
    prompt: str,
    validator,
    model_name: str,
    *,
    label: str,
    paper_title_en: str = "",
    doi: str = "",
) -> dict:
    original_model = os.environ.get("OPENAI_MODEL")
    effective_model = model_name or original_model or ""
    try:
        if model_name:
            os.environ["OPENAI_MODEL"] = model_name
        response = request_text_with_retry(
            prompt,
            validator,
            label=label,
            paper_title_en=paper_title_en,
            doi=doi,
        )
    finally:
        if original_model is None:
            os.environ.pop("OPENAI_MODEL", None)
        else:
            os.environ["OPENAI_MODEL"] = original_model
    if response is None:
        raise RuntimeError(f"llm request failed: {label}")
    return {"text": str(response), "model": effective_model}
