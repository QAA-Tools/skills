import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def current_shanghai_timestamp() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def append_jsonl_record(path: str | Path, record: dict) -> dict:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
    return record


def log_runtime_event(path: str | Path | None, *, source: str, event: str, status: str = "", **fields) -> dict | None:
    if path in (None, ""):
        return None
    record = {
        "ts": current_shanghai_timestamp(),
        "source": source,
        "event": event,
        "status": status,
    }
    record.update(fields)
    return append_jsonl_record(path, record)