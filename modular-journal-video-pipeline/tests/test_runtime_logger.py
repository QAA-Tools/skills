import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prl_llm_core  # noqa: E402
import runtime_logger  # noqa: E402
import stage3_llm_core  # noqa: E402


def test_log_runtime_event_appends_jsonl_record(tmp_path):
    log_path = tmp_path / "runtime_progress.jsonl"

    runtime_logger.log_runtime_event(
        log_path,
        source="stage2",
        event="row_started",
        status="running",
        paper_title_en="TDDFT Paper 1",
        attempt=1,
    )

    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["source"] == "stage2"
    assert rows[0]["event"] == "row_started"
    assert rows[0]["status"] == "running"
    assert rows[0]["paper_title_en"] == "TDDFT Paper 1"
    assert rows[0]["attempt"] == 1
    assert rows[0]["ts"]


def test_request_text_with_retry_logs_request_started_before_network_error(tmp_path, monkeypatch):
    log_path = tmp_path / "api_debug.jsonl"
    monkeypatch.setattr(prl_llm_core, "current_api_debug_log_path", lambda: log_path)
    monkeypatch.setattr(prl_llm_core, "call_openai_compatible", lambda prompt, *, system_prompt="": (_ for _ in ()).throw(TimeoutError("slow")))
    monkeypatch.setattr(prl_llm_core.time, "sleep", lambda _seconds: None)

    result = prl_llm_core.request_text_with_retry(
        "只返回数字 7，不要解释。",
        lambda text: text,
        label="score_ai:Journal A:0",
        paper_title_en="Journal A",
        doi="",
    )

    assert result is None
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["status"] == "request_started"
    assert rows[0]["stage"] == "score_ai"
    assert rows[1]["status"] == "network_error"


def test_stage3_request_json_with_retry_logs_request_started_before_network_error(tmp_path, monkeypatch):
    log_path = tmp_path / "stage3_api_debug.jsonl"
    monkeypatch.setattr(stage3_llm_core, "current_api_debug_log_path", lambda: log_path)
    monkeypatch.setattr(stage3_llm_core, "call_openai_compatible", lambda prompt, *, model_name, system_prompt="": (_ for _ in ()).throw(TimeoutError("slow")))
    monkeypatch.setattr(stage3_llm_core.time, "sleep", lambda _seconds: None)

    result = stage3_llm_core.request_json_with_retry(
        "返回一个 JSON 对象。",
        lambda payload: payload,
        label="page:Paper A",
        paper_title_en="Paper A",
        doi="10.1/test",
        model_name="gpt-5.5",
    )

    assert result is None
    rows = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[0]["status"] == "request_started"
    assert rows[0]["stage"] == "page"
    assert rows[1]["status"] == "network_error"