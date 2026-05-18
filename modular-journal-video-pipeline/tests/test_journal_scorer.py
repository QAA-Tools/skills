import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from journal_scorer import build_score_prompt, parse_numeric_score_response, score_journal_once  # noqa: E402


def test_build_score_prompt_requires_number_only_not_json():
    ai_prompt = build_score_prompt("Physical Review Letters", "ai")
    impact_prompt = build_score_prompt("Nature", "impact")
    assert "只返回一个数字" in ai_prompt
    assert "不要返回 JSON" in ai_prompt
    assert "小数点后 1 位" in ai_prompt
    assert "0 到 1" in ai_prompt
    assert "PRL 按 0.95 参考" in ai_prompt
    assert "只返回一个数字" in impact_prompt
    assert "不要返回 JSON" in impact_prompt
    assert "小数点后 1 位" in impact_prompt
    assert "Journal Impact Factor" in impact_prompt
    assert "非正式期刊、预印本平台、数据仓库返回 0" in impact_prompt



def test_parse_numeric_score_response_requires_pure_number():
    assert parse_numeric_score_response(" 9.5 ") == pytest.approx(9.5)
    with pytest.raises(ValueError, match="pure number"):
        parse_numeric_score_response('{"score": 9.5}')


def test_score_journal_once_delegates_to_llm_client():
    calls = []

    def fake_request(prompt: str, validator, model_name: str, **kwargs):
        calls.append((prompt, validator("0.95"), model_name, kwargs["label"]))
        return {"text": "0.95", "model": model_name}

    score = score_journal_once(
        "Physical Review Letters",
        "ai",
        sample_index=0,
        model_name="fake-model",
        request_text_fn=fake_request,
    )

    assert score == pytest.approx(0.95)
    assert len(calls) == 1
    assert "Physical Review Letters" in calls[0][0]
    assert calls[0][1] == pytest.approx(0.95)
    assert calls[0][2] == "fake-model"