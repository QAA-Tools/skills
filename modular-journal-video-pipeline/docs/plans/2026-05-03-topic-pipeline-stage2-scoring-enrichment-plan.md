# Topic Paper Pipeline Stage 2 Implementation Plan

> **For Hermes:** Focus only on stage 2 in this plan: journal scoring, gated metadata/abstract enrichment, and lightweight topic-match scoring. Do not revisit stage 1 retrieval/dedup and do not discuss later video scripting stages here.

**Goal:** Build stage 2 of the topic-paper pipeline: read `pool_stage1.jsonl`, write journal scores back into each paper record, skip expensive enrichment for low-journal-score papers, enrich the remaining records with missing abstract/basic metadata when needed, and finally compute a simple `topic_match_score` in `[0, 1]` for lightweight priority sorting.

**Architecture:** Add one standalone stage-2 script under `scripts/` with pure helper functions. The script should read line-delimited stage-1 records, process them in three passes (journal scoring → gated enrichment → topic-match scoring), and write updated line-delimited output files. Keep every decision explicit and deterministic; do not introduce LLM judging, vague heuristics, or extra score files.

**Tech Stack:** Python 3, JSONL files, `requests` only where needed for journal score lookup and metadata enrichment, existing repository `scripts/` conventions.

**Audit revision:** Keep stage 2 as one script with small pure helpers. Do not split into multiple classes or services unless implementation proves the single-file functional design is unmanageable.

## Design Philosophy Update

Keep a strict separation between **tools** and **flows**.

- Stage scripts are orchestration only. A stage main function should decide what work is missing for a record, call the right tool, merge the returned data, and write pipeline state.
- Reusable capabilities must live in standalone tool modules under `scripts/` rather than being embedded in one stage script.
- Tool modules should be callable from any pipeline or future project, not tied to this topic-paper flow.
- Configuration must stay with the tool layer. For example, an API client should accept task input such as a prompt or title, read its own URL/key/model settings from config, perform the request, and return structured results plus model/source metadata.
- Journal scoring, journal dictionary persistence, OpenAlex lookup, arXiv lookup, and future metadata/abstract resolution should all follow this pattern.
- When stage 1 input changes in the future, such as starting from RSS raw items instead of OpenAlex-first retrieval, the flow should be updated by recomposing the same tools, not by rewriting tool logic inside the stage script.

This plan should continue to evolve in that style: keep orchestration thin, keep tools reusable, and keep configuration separate from pipeline flow.

---

## Scope Freeze

This plan covers only:
1. read stage-1 pool records
2. write `journal_score` and `journal_score_source` back into each record
3. decide whether a record is worth enrichment based on journal score
4. enrich missing abstract/basic metadata for records that pass the gate
5. compute `topic_match_score` in `[0, 1]` from topic words against `title + abstract`
6. write stage-2 output JSONL files

Explicitly out of scope:
- re-running OpenAlex retrieval for the whole topic window
- fuzzy title dedup or cross-source pool merge beyond per-record enrichment
- LLM-based scoring
- content quality / video potential / composite final-score systems
- frequency-based keyword scoring
- any later script generation or rendering logic

---

## Proposed Files

### Create
- `scripts/topic_stage2_score_and_enrich.py`
- `tests/test_topic_stage2_score_and_enrich.py`
- `docs/plans/2026-05-03-topic-pipeline-stage2-scoring-enrichment-plan.md` (this file)

### Reuse / inspect but avoid modifying unless necessary
- `scripts/topic_stage1_openalex_pool.py`
- `scripts/prl_rss_extract.py`
- `scripts/prepare_issue.py`

---

## Input Contract

Stage 2 reads one or more line-delimited OpenAlex snapshot files and/or an existing global pool file.

Latest agreed boundary:
- Stage 1 may use a retrieval keyword or method term such as `TDDFT` to collect raw literature.
- Stage 2 is completely independent of the final video topic and must not require or depend on that topic in order to ingest, score journals, or enrich missing fields.
- Stage 3 is the first stage that should accept the current video `topic_query` and use it for topic-sensitive selection.

Therefore, stage 2 must not depend on the config file's `topic_query` for its core processing contract. Retrieval intent belongs to stage 1 snapshot provenance; topic-sensitive selection belongs to stage 3. Stage 2 should keep only source metadata such as snapshot path and batch timestamp.

Each row must already contain at least:
- `record_id`
- `source`
- `title`
- `abstract`
- `publication_date`
- `journal`
- `doi`
- `paper_url`
- `authors`
- `first_author`

Stage 2 must tolerate empty values for `abstract`, `doi`, `paper_url`, and `authors`.

---

## Output Contract

Stage 2 should write **one stable final output** and may optionally write two debug/intermediate artifacts under a caller-provided output directory.

### Stable required output: `pool_stage2_final.jsonl`
Contains the full pool row plus stage-2 processing fields:

```json
{
  "record_id": "oa_000001",
  "journal_score": 9.2,
  "journal_score_source": "local|api|missing",
  "abstract_source": "openalex|arxiv|missing",
  "enrichment_status": "skipped_low_journal|not_needed|done|partial|failed"
}
```

Required semantics:
- `pool_stage2_final.jsonl` keeps the **same row count and row order** as the input pool after merge/update for this run
- low-journal-score records are **not** removed from the final output; they simply carry `enrichment_status = "skipped_low_journal"`
- stage 2 does **not** compute or persist a topic-match field as part of the stable contract
- stage 2 may be run repeatedly; each run should advance the pool state for only the rows actually processed in that run, without forcing a full re-pass

### Optional debug artifact: `pool_stage2_scored.jsonl`
Contains full records immediately after journal scoring. Only write this file when debug/intermediate output is enabled.

### Optional debug artifact: `pool_stage2_enriched.jsonl`
Contains full records immediately after the enrichment pass. Only write this file when debug/intermediate output is enabled.

Notes:
- stage 2 does **not** create a separate per-run journal-score result JSON file; journal score lives inside each paper record
- repeated-run state should be observable via clear summary fields and a durable run log
- if optional intermediate files are enabled, they must also preserve the full input row count and stable row order

---

## Config Contract

Use a minimal JSON config for stage 2. Example:

```json
{
  "stage1_pool": "/tmp/topic-stage1-demo/pool_stage1.jsonl",
  "outdir": "/tmp/topic-stage2-demo",
  "journal_score_threshold": 5.0,
  "enable_arxiv_enrichment": false,
  "write_intermediates": false,
  "score_batch_limit": 5,
  "run_log_path": "/tmp/topic-stage2-demo/pool.run_log.jsonl"
}
```

Required fields:
- `stage1_pool`
- `outdir`
- `journal_score_threshold`

Optional fields:
- `enable_arxiv_enrichment` (default `false`)
- `write_intermediates` (default `false`)
- `score_batch_limit` (default unlimited; when set, count by **effective processed rows**, not raw scanned rows)
- `run_log_path` (default derived from the stable pool path)

Do not add broader ranking knobs yet.

---

## Journal Score Contract

Journal score is the first gate in stage 2.

Rules:
- every record gets `journal_score`
- every record gets `journal_score_source`
- a record is **eligible for enrichment in this run** when `journal_score >= journal_score_threshold`
- if journal score is unavailable, set `journal_score` to `null` and `journal_score_source` to `"missing"`; treat the record as not eligible for enrichment in this run

Important boundary:
- journal score is stored directly in the paper record
- stage 2 must not emit a separate “journal score output file” for the batch
- the enrichment gate is a runtime decision derived from `journal_score` and the current config threshold; do not persist a separate `passes_enrichment_gate` field in the stable output
- whether journal-score lookup internally uses an in-code map, an existing local dictionary, or an API fallback is an implementation detail; the stage-2 record contract stays the same

This gate exists to avoid spending enrichment work on journals that are clearly too low-priority for this pipeline.

---

## Enrichment Contract

Only records that are eligible under the current journal-score threshold are eligible for enrichment attempts.

Enrichment goals:
- fill missing abstract if possible
- fill missing authors if possible
- fill missing `paper_url` if possible
- leave already-good fields untouched

Enrichment rules:
- if a record already has a usable abstract, set `enrichment_status = "not_needed"`
- if a record fails the journal gate, do not attempt enrichment; set `enrichment_status = "skipped_low_journal"`
- if enrichment fills all targeted missing fields, set `enrichment_status = "done"`
- if enrichment fills some but not all targeted missing fields, set `enrichment_status = "partial"`
- if enrichment is attempted and nothing useful is recovered, set `enrichment_status = "failed"`

Source rules:
- keep `abstract_source = "openalex"` when the original abstract is already present and retained
- use `abstract_source = "arxiv"` only when an arXiv match is used to fill a missing abstract
- if abstract stays missing, use `abstract_source = "missing"`

Matching boundary:
- enrichment is per-record and conservative
- when arXiv matching is used, title is the primary key and author is only auxiliary confirmation
- if the match is uncertain, do not fill guessed content

---

## Repeated-Run Processing Contract

Stage 2 is expected to be run many times rather than only once.

Rules:
- each run may stop after a caller-provided `score_batch_limit`
- the limit counts **effective processed rows** only
- a row counts toward the limit only when stage 2 actually advances useful work for that row in this run, such as:
  - filling missing journal scores
  - reusing/copying journal scores for a same-journal row
  - attempting missing-abstract enrichment
  - recording a terminal `not_found` abstract result after a real lookup attempt
- merely scanning a row without doing work must not consume the batch quota
- when there is still work left after the current run, return a clear progress status such as `OK_PROGRESS`
- when no work remains, return a clear terminal status such as `OK_NO_REMAINING`
- every run must append a JSONL run-log entry summarizing at least processed count, failed count, remaining count, and timestamp

Important boundary:
- repeated-run behavior is part of the stage-2 core contract, not an optional operator trick
- stage 2 should be safe to resume without duplicating already-finished work

---

## Suggested Internal Function Layout

Inside `scripts/topic_stage2_score_and_enrich.py`, aim for this order:

1. imports / constants
2. `read_jsonl`
3. `write_jsonl`
4. `load_stage2_config`
5. `lookup_journal_score`
6. `apply_journal_scores`
7. `has_usable_abstract`
8. `enrich_record`
9. `apply_enrichment`
10. `tokenize_topic_words`
11. `tokenize_text_words`
12. `prefix_similarity`
13. `score_topic_word`
14. `compute_topic_match_score`
15. `apply_topic_scores`
16. `run_stage2`
17. CLI `main()`

Keep journal scoring, enrichment, and topic scoring separate.

---

## Implementation Tasks

### Task 1: Create the test file and lock the stage-2 public contract

**Objective:** Define the record-level behavior of stage 2 before implementation.

**Files:**
- Create: `tests/test_topic_stage2_score_and_enrich.py`

**Step 1: Write failing tests for journal-score fields and gate behavior**

Include tests for:
- score written back into record
- `journal_score_source` written back into record
- enrichment eligibility at, above, and below the threshold
- missing journal score yields `null + missing` and is not enrichment-eligible in this run

Suggested tests:

```python
from scripts.topic_stage2_score_and_enrich import apply_journal_scores, is_enrichment_eligible


def test_apply_journal_scores_sets_score_and_source():
    rows = [{"record_id": "oa_1", "journal": "Physical Review Letters"}]

    def fake_lookup(name):
        return 9.2, "local"

    out = apply_journal_scores(rows, threshold=5.0, lookup_fn=fake_lookup)
    assert out[0]["journal_score"] == 9.2
    assert out[0]["journal_score_source"] == "local"
    assert is_enrichment_eligible(out[0], 5.0) is True


def test_apply_journal_scores_marks_missing_scores_as_not_eligible():
    rows = [{"record_id": "oa_1", "journal": "Unknown Journal"}]

    def fake_lookup(name):
        return None, "missing"

    out = apply_journal_scores(rows, threshold=5.0, lookup_fn=fake_lookup)
    assert out[0]["journal_score"] is None
    assert out[0]["journal_score_source"] == "missing"
    assert is_enrichment_eligible(out[0], 5.0) is False


def test_is_enrichment_eligible_is_true_at_threshold_boundary():
    row = {"journal_score": 5.0}
    assert is_enrichment_eligible(row, 5.0) is True
```

**Step 2: Run tests to verify failure**

Run:
```bash
pytest -q tests/test_topic_stage2_score_and_enrich.py
```

Expected: FAIL — module/functions do not exist yet.

---

### Task 2: Create the script skeleton and implement journal-score helpers

**Objective:** Implement the minimum journal-score pass needed by the first tests.

**Files:**
- Create: `scripts/topic_stage2_score_and_enrich.py`
- Test: `tests/test_topic_stage2_score_and_enrich.py`

**Step 1: Add module skeleton**

Required functions in initial skeleton:
- `read_jsonl(path)`
- `write_jsonl(path, rows)`
- `lookup_journal_score(journal_name)`
- `is_enrichment_eligible(row, threshold)`
- `apply_journal_scores(rows, threshold, lookup_fn=None)`

**Step 2: Implement `apply_journal_scores()`**

Rules:
- preserve original record fields
- write `journal_score`
- write `journal_score_source`
- keep row order stable
- keep enrichment gating as a runtime helper (`is_enrichment_eligible`) rather than a persisted stable-output field

**Step 3: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage2_score_and_enrich.py
```

Expected: PASS for the journal-score helper tests.

---

### Task 3: Add enrichment tests for gate behavior and field updates

**Objective:** Lock down which records are enriched and how enrichment status is written.

**Files:**
- Modify: `tests/test_topic_stage2_score_and_enrich.py`
- Modify: `scripts/topic_stage2_score_and_enrich.py`

**Step 1: Add tests for enrichment gate behavior**

Cover at least:
- low-journal-score row becomes `skipped_low_journal`
- low-journal-score row does **not** call the enrichment helper
- row with existing abstract becomes `not_needed`
- row with missing abstract and successful fill becomes `done`
- row with partial recovery becomes `partial`
- row with no useful recovery becomes `failed`

**Step 2: Add enrichment helper function**

Implement something like:
- `enrich_record(row, *, enable_arxiv_enrichment=False) -> dict`
- `apply_enrichment(rows, threshold, *, enrich_fn=None, enable_arxiv_enrichment=False) -> list[dict]`

**Step 3: Preserve conservative update rules**

Rules:
- do not overwrite a non-empty abstract
- do not overwrite non-empty authors with emptier data
- do not overwrite non-empty `paper_url` with emptier data
- update only targeted missing fields

**Step 4: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage2_score_and_enrich.py
```

Expected: PASS.

---

### Task 4: Add topic tokenization and word-scoring tests

**Objective:** Lock down the simple `topic_match_score` rules.

**Files:**
- Modify: `tests/test_topic_stage2_score_and_enrich.py`
- Modify: `scripts/topic_stage2_score_and_enrich.py`

**Step 1: Add tests for tokenization**

Cover:
- punctuation splitting
- lowercase normalization
- dropping empty tokens
- dropping tokens shorter than 2 characters
- empty cleaned topic yields an empty topic-word list

**Step 2: Stage-3 ownership of topic scoring**

Topic-word tokenization and `compute_topic_match_score(...)` are now treated as **stage-3 selection helpers**, not stage-2 responsibilities.

Current boundary:
- Stage 2 does not compute, persist, or validate any topic-match field.
- Stage 3 accepts the current `topic_query` and computes topic match locally at selection time.
- Repeated keyword frequency still must not inflate the final topic score, but that rule is verified in stage-3 tests rather than stage-2 tests.

**Step 3: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py
```

Expected: PASS.

---

### Task 5: Add config loader and pipeline entrypoint tests

**Objective:** Turn the helpers into a runnable stage-2 script.

**Files:**
- Modify: `tests/test_topic_stage2_score_and_enrich.py`
- Modify: `scripts/topic_stage2_score_and_enrich.py`

**Step 1: Add config loader tests**

Cover:
- minimal valid config
- missing required fields
- nonexistent input snapshot path
- threshold or batch-limit validation
- repeated-run state / run-log semantics

**Step 2: Implement config loader**

Suggested function:
- `load_stage2_config(path: str | Path) -> dict`

Validate required fields:
- `openalex_inputs`
- `pool_path`
- `journal_ai_threshold`
- `journal_impact_threshold`

**Step 3: Implement high-level runner**

Suggested function:
- `run_stage2(config: dict) -> dict`

Responsibilities:
1. read one or more OpenAlex snapshot files and merge/update the global pool
2. apply journal scores
3. optionally enrich missing abstracts
4. persist updated pool rows and append a run log
5. never compute or persist topic-match fields
6. return a concise summary dict

**Step 4: Add CLI entrypoint**

Suggested CLI:
```bash
/usr/bin/python3 scripts/topic_stage2_score_and_enrich.py --config /path/to/config.json
```

Print only concise summary, for example:
```text
stage1=74
final=74
intermediates_written=no
outdir=/tmp/topic-stage2-demo
```

**Step 5: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage2_score_and_enrich.py
```

Expected: PASS.

---

### Task 6: Add end-to-end output contract test

**Objective:** Verify the full stage-2 file contract and field presence.

**Files:**
- Modify: `tests/test_topic_stage2_score_and_enrich.py`
- Modify: `scripts/topic_stage2_score_and_enrich.py`

**Step 1: Add an e2e test with fake journal lookup and fake enrichment**

Expected outputs in temp dir:
- `pool_stage2_final.jsonl`
- optionally `pool_stage2_scored.jsonl` when `write_intermediates=true`
- optionally `pool_stage2_enriched.jsonl` when `write_intermediates=true`

Assertions:
- `pool_stage2_final.jsonl` exists
- final rows include `journal_score`, `journal_score_source`, `abstract_source`, `enrichment_status`, `topic_match_score`
- final row count equals input row count
- final row order remains stable
- optional intermediate files appear only when explicitly enabled

**Step 2: Run the single e2e test**

Run:
```bash
pytest -q tests/test_topic_stage2_score_and_enrich.py::test_run_stage2_writes_expected_files
```

Expected: PASS.

**Step 3: Run full test file**

Run:
```bash
pytest -q tests/test_topic_stage2_score_and_enrich.py
```

Expected: PASS.

---

## Review Checklist for the Implementer

Before calling stage 2 done, confirm:
- [ ] stage 2 reads `pool_stage1.jsonl` and does not redo stage-1 retrieval
- [ ] config `topic_query` is the single scoring source for the run
- [ ] journal score is stored back into each paper record, not emitted as a separate batch score file
- [ ] enrichment eligibility is derived from `journal_score` and threshold at runtime, not persisted as a redundant stable-output field
- [ ] records below the journal threshold do not attempt enrichment
- [ ] enrichment leaves already-good fields untouched
- [ ] `topic_match_score` is computed from per-topic-word **best** matches only
- [ ] repeated keyword frequency does not increase `topic_match_score`
- [ ] exact token match gives `1.0`
- [ ] empty cleaned topic returns `0.0` rather than erroring
- [ ] final output keeps the same row count and row order as stage-1 input
- [ ] tests use mocking/fakes; no live network in unit tests

---

## Verification Commands

After implementation, run exactly:

```bash
pytest -q tests/test_topic_stage2_score_and_enrich.py
python3 -m py_compile scripts/topic_stage2_score_and_enrich.py
```

If a manual smoke run is needed:

```bash
cat >/tmp/topic_stage2_demo.json <<'JSON'
{
  "topic_query": "moire exciton twisted bilayer",
  "stage1_pool": "/tmp/topic-stage1-demo/pool_stage1.jsonl",
  "outdir": "/tmp/topic-stage2-demo",
  "journal_score_threshold": 5.0,
  "enable_arxiv_enrichment": false,
  "write_intermediates": false
}
JSON

/usr/bin/python3 scripts/topic_stage2_score_and_enrich.py --config /tmp/topic_stage2_demo.json
```

Expected outputs:
- `/tmp/topic-stage2-demo/pool_stage2_final.jsonl`
- optionally `/tmp/topic-stage2-demo/pool_stage2_scored.jsonl` when `write_intermediates=true`
- optionally `/tmp/topic-stage2-demo/pool_stage2_enriched.jsonl` when `write_intermediates=true`

---

## Notes on Deliberate Simplifications

These simplifications are intentional for stage 2:
- no LLM scoring
- no “video potential” or “content quality” score
- no frequency bonus for repeated keyword appearances
- no semantic query expansion
- no batch-level extra score JSON artifact
- no hard rejection based on `topic_match_score`; it is for lightweight priority ordering only

This keeps stage 2 explainable, cheap to debug, and tightly aligned with the actual fields that matter downstream.


---

## Deferred Future Extension Note

After the current stage-2 scoring contract is stable, a future extension may add explicit non-physics field scores such as `score_journal_field_ml` to mark how strongly a journal belongs to another domain like machine learning. This is intentionally deferred: the current priority is to keep `score_journal_ai` narrowly defined as a 0-to-1 physics-relevance auxiliary score rather than widening the live contract now.
