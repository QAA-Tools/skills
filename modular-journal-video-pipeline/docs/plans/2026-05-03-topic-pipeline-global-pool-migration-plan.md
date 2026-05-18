# Topic Paper Pipeline Global Pool Migration Plan

> **For Hermes:** Migrate the topic-paper pipeline from per-run stage outputs to a global pool database. Implement with strict TDD.

**Goal:** Make OpenAlex snapshots append-only timestamped input files, then let stage 2 incrementally build/update one global paper pool and let stage 3 update generated content back into that same pool.

**Architecture:** Keep small standalone scripts with pure helpers. Stage 1 saves timestamped OpenAlex raw JSONL snapshots. Stage 2 reads one or more snapshot files, parses/merges records into a single pool JSONL database with prefixed field names and API-based journal scores, and is completely independent of the final video topic. Stage 3 reads that same pool, accepts the current `topic_query`, re-ranks/selects records for the current video topic, generates content, and writes the updated pool back with per-field timestamps/models.

**Tech Stack:** Python 3, JSON/JSONL, existing `requests` usage, existing `prl_llm_core.py` request/validator helpers, pytest.

---

## Planned work

1. Add/adjust tests for the new global-pool contracts before code changes.
2. Extend stage 1 to support timestamped OpenAlex snapshot filenames without per-run subdirectories.
3. Rewrite stage 2 around a global pool schema:
   - ingest multiple OpenAlex snapshot files
   - parse and deduplicate by normalized title
   - merge into a single pool JSONL database
   - compute API-sampled journal AI / impact scores with trimmed-mean aggregation
   - keep stage 2 completely independent of the final video `topic_query`; stage 2 must not know or depend on the current video topic to score/process the pool
   - support repeated runs with a caller-controlled batch limit on **effective processed rows**
   - when nothing remains to process, return a clear terminal status rather than silently doing nothing
   - append one JSONL run-log record per run so progress across repeated runs is auditable
   - keep prefixed field names and audit timestamps
4. Implement stage 3 content updates directly on the pool:
   - deterministic record selection
   - accept the current `topic_query` at stage 3 time rather than inheriting a fixed stage-2 topic
   - allow stage-1 retrieval topic and stage-3 video topic to differ, so one broad/method pool can later support several downstream topics
   - per-record brief/key_points/title_zh/voice fields
   - per-field updated timestamp and model
   - preserve pool rows, update in place
5. Add `docs/pool_schema.md` documenting every key and naming rule.
6. After the contracts stabilize, fold the final agreed behavior into the repo README so future runs rely on one canonical operator-facing description.
7. Run focused tests, then full relevant tests, then a minimal end-to-end local verification.
