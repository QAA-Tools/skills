# Topic Paper Pipeline Stage 3 Implementation Plan

> **For Hermes:** Focus only on stage 3 in this plan: API-based generation of `title_zh`, `brief`, `key_points`, `voice_intro`, and `voice_points` from the stage-2 final pool. Do not revisit stage 1 retrieval/dedup or stage 2 scoring/enrichment logic here.

**Goal:** Build stage 3 of the topic-paper pipeline: read `pool_stage2_final.jsonl`, select the papers to generate, call the OpenAI-compatible API with simple PRL-style prompts, validate the returned structure, and write a stable content-generation output file that later rendering steps can consume directly.

**Architecture:** Add one standalone stage-3 script under `scripts/` with pure helper functions and a small CLI. The script should load the final stage-2 pool, pick the top `selected_n` rows in a deterministic way, call the API separately for page copy, voice copy, and title translation, and write one stable JSON output. Keep the first version intentionally close to the existing PRL pipeline rather than over-optimizing prompts or inventing a more abstract content system too early.

**Tech Stack:** Python 3, JSON/JSONL files, stage-3-local OpenAI-compatible request helpers, existing repository `scripts/` conventions.

**Audit revision:** Keep stage 3 simple and PRL-shaped for the first implementation. Reuse existing prompt/validator patterns where possible. Do not introduce prompt orchestration frameworks, extra ranking models, or new multi-stage LLM workflows in this phase.

---

## Scope Freeze

This plan covers only:
1. read `pool_stage2_final.jsonl`
2. deterministically select the records that will be sent to API generation
3. build API prompts for title translation, page key points, and voice intro/points
4. validate API outputs before accepting them
5. assemble a stable `input_stage3.json`-style output payload
6. write optional debug artifacts such as prompt text and API log

Explicitly out of scope:
- stage 1 retrieval or stage 2 scoring changes
- prompt over-optimization beyond PRL-style baseline prompts
- rendering video/pages/audio
- publish description/tag generation for the final platform post
- automated prompt tuning
- introducing extra subjective scores like “video potential”

---

## Proposed Files

### Create
- `scripts/topic_stage3_generate_content.py`
- `tests/test_topic_stage3_generate_content.py`
- `docs/plans/2026-05-03-topic-pipeline-stage3-api-content-plan.md` (this file)

### Reuse / inspect but avoid modifying unless necessary
- existing PRL pipeline prompt / validator patterns as reference only
- `scripts/enrich_issue_llm.py`
- `scripts/topic_stage2_score_and_enrich.py`

---

## Input Contract

Stage 3 reads the stable final output from stage 2:
- `pool_stage2_final.jsonl`

`pool_stage2_final.jsonl` is now treated as a global pool rather than a single-topic run artifact.

Latest agreed boundary:
- Stage 1 may collect papers with a retrieval-oriented topic or method keyword.
- Stage 2 builds and maintains the pool without needing to know the final video topic.
- Stage 3 accepts the **current** `topic_query` and performs the topic-sensitive selection at generation time.

This means one pool built from a broad query such as `TDDFT` can later be re-used to generate videos for narrower downstream topics.

Each row must already contain at least:
- `record_id`
- `title`
- `abstract`
- `journal`
- `publication_date`
- `doi`
- `paper_url`
- `authors`
- `journal_score`
- `enrichment_status`
- `abstract_source`

Stage 3 must tolerate empty values for `abstract`, `doi`, `paper_url`, and `authors`, but must skip rows that do not have a usable English title.

---

## Selection Contract

Stage 3 needs a deterministic rule for choosing which rows are sent to API generation.

### Required selection rules
- input rows are first sorted by:
  1. `journal_score` descending (missing scores sort last)
  2. stage-3-local `topic_match_score` descending
  3. original input order as final tie-breaker
- select the top `selected_n` rows after this ordering
- if the pool has fewer than `selected_n` rows, select all available rows
- rows not selected for generation should still be represented in a lightweight `other_papers` section in the final output
- selected rows that ultimately fail generation after retries must still remain traceable in a dedicated `failed_papers` section

### Topic-match rules

Stage 3 computes topic match locally from the current `topic_query` against the best available paper text (`title + abstract`).

Rules:
- lowercase
- split topic and paper text on whitespace / punctuation
- use exact token match first, then simple prefix similarity as a lightweight fallback
- for each topic word, keep only the best match score found in the paper text
- do not add frequency bonuses for repeated words
- final topic match is the average of per-word best scores and must stay in `[0, 1]`

### Important boundary
- stage 3 must not rely on a precomputed stage-2 topic field to perform final selection
- stage 3 selection is deterministic and fully local; no extra API call is used to “decide” which papers survive
- if later needed, stage 3 may persist the current run's topic metadata for audit/debug, but the selection logic itself belongs to stage 3

---

## Output Contract

Stage 3 should write one stable required output and may optionally write prompt/debug artifacts under a caller-provided output directory.

### Stable required output: `input_stage3.json`

The output should follow a PRL-like structure so later rendering can consume it with minimal adaptation.

```json
{
  "topic_query": "moire exciton twisted bilayer",
  "selected_n": 8,
  "papers": [
    {
      "record_id": "oa_000001",
      "title_en": "Original English title",
      "title_zh": "中文标题",
      "doi": "10.1103/...",
      "paper_url": "https://doi.org/...",
      "brief": "一句话核心介绍。",
      "key_points": ["...。", "...。", "...。", "...。"],
      "voice_intro": "一句话核心介绍。",
      "voice_points": ["...。", "...。"]
    }
  ],
  "other_papers": [
    {
      "record_id": "oa_000009",
      "title_en": "Unselected English title",
      "title_zh": "",
      "doi": "10.1103/..."
    }
  ],
  "failed_papers": [
    {
      "record_id": "oa_000010",
      "title_en": "Selected but failed title",
      "doi": "10.1103/...",
      "failure_stage": "page|voice|title",
      "failure_reason": "validation_failed"
    }
  ]
}
```

Required semantics:
- `papers` keeps the selected order produced by the selection contract
- `brief` is **not** generated by a separate API call in v1; it is copied directly from validated `voice_intro`, matching the current PRL pattern
- `key_points` must be a list of 4~6 clean Chinese sentences; this is a stage-3-specific contract and should be enforced even if reused PRL helpers are looser
- `voice_points` must be a list of 1~2 clean Chinese sentences; this is a stage-3-specific contract on top of the simpler PRL baseline
- `other_papers` contains only unselected rows as lightweight audit placeholders; keep it minimal and never expand it into full render content
- `failed_papers` contains selected rows that still failed generation after retries so no selected record disappears silently from the final JSON

### Optional debug artifacts
- `stage3_prompt.txt` — optional combined reference prompt or run note
- `api_debug.jsonl` — optional per-call debug log
- `stage3_selected_preview.json` — optional selected records before API generation

Notes:
- stage 3 is the first place where API-generated content becomes the main payload
- stable output is JSON rather than JSONL because downstream render usually wants issue-level grouped structure
- debug artifacts are optional and must not be required by downstream consumers

---

## Config Contract

Use a minimal JSON config for stage 3. Example:

```json
{
  "topic_query": "moire exciton twisted bilayer",
  "stage2_final": "/tmp/topic-stage2-demo/pool_stage2_final.jsonl",
  "outdir": "/tmp/topic-stage3-demo",
  "selected_n": 8,
  "llm_mode": "api",
  "write_debug_artifacts": true
}
```

Required fields:
- `topic_query`
- `stage2_final`
- `outdir`
- `selected_n`
- `llm_mode`

Optional fields:
- `write_debug_artifacts` (default `true`)

`selected_n` rules:
- must be a positive integer
- v1 hard bound: `1 <= selected_n <= 20`
- values outside this range should raise a clear config error

Valid `llm_mode` values:
- `api`
- `fake`
- `auto`

Boundary:
- stage 3 must support `fake` mode for tests and offline contract validation
- production/default expectation for this stage is `api`

---

## API Generation Contract

Stage 3 should intentionally mimic the current PRL API-generation shape.

For each selected paper, generate exactly three payloads:
1. `page_payload` → yields `key_points`
2. `voice_payload` → yields `voice_intro` and `voice_points`
3. `title_payload` → yields `title_zh`

In v1, `brief` is **not** a fourth payload. It is copied from the validated `voice_intro` after `voice_payload` succeeds.

### Prompt strategy
- start with simple PRL-like prompts rather than an over-engineered prompt system
- prompts should only use the paper's existing title and abstract/basic metadata
- prompts must not inject external facts
- prompt refinement is a later optimization pass, not part of initial stage-3 architecture

### Data source boundary
- primary content inputs are:
  - `title`
  - `abstract`
- optional supporting inputs are:
  - `journal`
  - `publication_date`
- do not depend on long author lists or unrelated metadata unless the prompt explicitly needs them

---

## Validation Contract

Every API response must be validated before the paper is accepted into `papers`.

### `title_payload` validation
- must return exactly one non-empty Chinese title string
- should not include JSON wrappers, explanations, or extra lines

### `page_payload` validation
- must produce `key_points` as a list
- `key_points` length must be between 4 and 6
- every point must be a clean Chinese sentence ending with `。`
- reject points containing obvious JSON shell fragments like `points`, `key_points`, `items`, `{`, `[` when used as structural garbage
- this `4~6` rule is a stage-3-specific post-check; do not assume existing PRL validators already enforce it exactly

### `voice_payload` validation
- must produce `voice_intro` as one non-empty sentence
- `voice_points` must be a list of 1~2 clean Chinese sentences
- `voice_intro` and `voice_points` should not contain scaffolding text or explanations
- this `voice_points` requirement is a stage-3-specific contract; reuse PRL cleaning/retry helpers, but do not assume the old PRL validator is sufficient by itself

### Failure handling
- if a single generation call fails validation, retry using the same prompt pattern and the existing retry helper strategy
- if a selected paper still fails after retries, do not silently drop it; append a minimal record to `failed_papers` and continue
- if all selected papers fail, raise a clear runtime error

---

## Suggested Internal Function Layout

Inside `scripts/topic_stage3_generate_content.py`, aim for this order:

1. imports / constants
2. `read_jsonl`
3. `load_stage3_config`
4. `normalize_stage2_row`
5. `sort_and_select_rows`
6. `build_stage3_page_prompt`
7. `build_stage3_voice_prompt`
8. `build_stage3_title_prompt`
9. `validate_stage3_page_payload`
10. `validate_stage3_voice_payload`
11. `validate_stage3_title_payload`
12. `generate_one_paper`
13. `build_other_papers`
14. `run_stage3`
15. CLI `main()`

Where practical, reuse the retry/request/cleanup pattern from the PRL pipeline, but keep the implementation in a stage-3-local neutral helper module rather than importing a `prl_*` core directly. Stage-3-specific output constraints remain defined by this plan and should not be assumed to come for free from the old PRL validators.

---

## Implementation Tasks

### Task 1: Create the test file and lock the stage-3 output schema

**Objective:** Define the record-level and issue-level stage-3 contract before implementation.

**Files:**
- Create: `tests/test_topic_stage3_generate_content.py`

**Step 1: Write failing tests for selection and output shape**

Include tests for:
- deterministic sorting by `journal_score`, then `topic_match_score`, then original order
- top `selected_n` become `papers`
- remainder become `other_papers`
- `brief == voice_intro` in v1 output
- `selected_n` values outside `1..20` fail config validation

Suggested tests:

```python
from scripts.topic_stage3_generate_content import sort_and_select_rows


def test_sort_and_select_rows_uses_stage2_scores_then_input_order():
    rows = [
        {"record_id": "a", "title": "A", "journal_score": 9.0, "topic_match_score": 0.6},
        {"record_id": "b", "title": "B", "journal_score": 9.0, "topic_match_score": 0.8},
        {"record_id": "c", "title": "C", "journal_score": 8.0, "topic_match_score": 0.9},
    ]
    selected, other = sort_and_select_rows(rows, selected_n=2)
    assert [r["record_id"] for r in selected] == ["b", "a"]
    assert [r["record_id"] for r in other] == ["c"]
```

**Step 2: Run tests to verify failure**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py
```

Expected: FAIL — module/functions do not exist yet.

---

### Task 2: Create the script skeleton and implement selection helpers

**Objective:** Implement the deterministic stage-3 selection logic.

**Files:**
- Create: `scripts/topic_stage3_generate_content.py`
- Test: `tests/test_topic_stage3_generate_content.py`

**Step 1: Add module skeleton**

Required functions in initial skeleton:
- `read_jsonl(path)`
- `load_stage3_config(path)`
- `normalize_stage2_row(row)`
- `sort_and_select_rows(rows, selected_n)`
- `build_other_papers(rows)`

**Step 2: Implement selection rules**

Rules:
- preserve original row data during normalization
- stable tie-break by original input order
- rows with empty usable title are excluded from selected generation but may still be tracked in `other_papers`

**Step 3: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py
```

Expected: PASS for selection-helper tests.

---

### Task 3: Add prompt-builder and validator tests

**Objective:** Lock down the PRL-like API-generation contract without overcomplicating prompts.

**Files:**
- Modify: `tests/test_topic_stage3_generate_content.py`
- Modify: `scripts/topic_stage3_generate_content.py`

**Step 1: Add tests for prompt builders**

Cover:
- title prompt includes the paper title and abstract
- page prompt asks for 4~6 plain Chinese sentences
- voice prompt asks for one intro sentence and 1~2 supporting points

**Step 2: Add tests for validators**

Cover:
- valid `title_payload`
- valid `page_payload`
- valid `voice_payload`
- invalid JSON-shell pollution in `key_points`
- invalid sentence count in `key_points`
- invalid `voice_points` count
- empty abstract still allows prompts/validators to run on title-only input

**Step 3: Implement helpers**

Implement something like:
- `build_stage3_page_prompt(row: dict) -> str`
- `build_stage3_voice_prompt(row: dict) -> str`
- `build_stage3_title_prompt(row: dict) -> str`
- `validate_stage3_page_payload(data) -> dict | None`
- `validate_stage3_voice_payload(data) -> dict | None`
- `validate_stage3_title_payload(data) -> dict | None`

**Step 4: Reuse existing PRL conventions where possible**

Prefer reusing the existing cleaning and retry pattern in a stage-3-local neutral helper instead of binding topic stage 3 directly to a `prl_*` module.

**Step 5: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py
```

Expected: PASS.

---

### Task 4: Add single-paper generation tests

**Objective:** Lock down how one selected row becomes one generated paper payload.

**Files:**
- Modify: `tests/test_topic_stage3_generate_content.py`
- Modify: `scripts/topic_stage3_generate_content.py`

**Step 1: Add test with fake generation helpers**

Cover:
- successful page/voice/title generation becomes one paper record
- `brief` equals `voice_intro`
- no separate `brief` request is issued; only page/voice/title requests are made
- `key_points` copied from validated page payload
- `voice_points` copied from validated voice payload
- `doi` and `paper_url` preserved from stage-2 row

**Step 2: Add failure-handling test**

Cover:
- one failed selected paper is skipped from `papers` after retries
- the same failed selected paper is recorded in `failed_papers`
- run continues if at least one other paper succeeds
- run raises clear error if zero papers succeed

**Step 3: Implement generation helper**

Implement something like:
- `generate_one_paper(row, *, request_fn=None) -> dict | None`

**Step 4: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py
```

Expected: PASS.

---

### Task 5: Add config loader and high-level runner tests

**Objective:** Turn the helpers into a runnable stage-3 script.

**Files:**
- Modify: `tests/test_topic_stage3_generate_content.py`
- Modify: `scripts/topic_stage3_generate_content.py`

**Step 1: Add config loader tests**

Cover:
- minimal valid config
- missing required fields
- nonexistent `stage2_final`
- invalid `selected_n`
- `selected_n` above the hard upper bound
- invalid `llm_mode`

**Step 2: Implement config loader**

Suggested function:
- `load_stage3_config(path: str | Path) -> dict`

Validate required fields:
- `topic_query`
- `stage2_final`
- `outdir`
- `selected_n`
- `llm_mode`

**Step 3: Implement high-level runner**

Suggested function:
- `run_stage3(config: dict) -> dict`

Responsibilities:
1. read stage-2 final rows
2. normalize and sort/select rows
3. optionally write selected preview when debug artifacts are enabled
4. generate `papers` content via API/fake mode
5. collect any selected-but-failed rows into `failed_papers`
6. build minimal `other_papers`
7. write `input_stage3.json`
8. optionally write prompt/debug artifacts
9. return a concise summary dict

**Step 4: Add CLI entrypoint**

Suggested CLI:
```bash
/usr/bin/python3 scripts/topic_stage3_generate_content.py --config /path/to/config.json
```

Print only concise summary, for example:
```text
stage2_rows=74
selected=8
generated=8
other_papers=66
out=/tmp/topic-stage3-demo/input_stage3.json
```

**Step 5: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py
```

Expected: PASS.

---

### Task 6: Add end-to-end output contract test

**Objective:** Verify the final stage-3 file contract and field presence.

**Files:**
- Modify: `tests/test_topic_stage3_generate_content.py`
- Modify: `scripts/topic_stage3_generate_content.py`

**Step 1: Add an e2e test with fake generation**

Expected outputs in temp dir:
- `input_stage3.json`
- optionally `api_debug.jsonl` when debug artifacts are enabled
- optionally `stage3_selected_preview.json` when debug artifacts are enabled

Assertions:
- `input_stage3.json` exists
- `papers` count equals number of successfully generated selected papers
- `other_papers` count matches unselected rows
- `failed_papers` count matches selected rows that still failed generation
- every paper contains `title_en`, `title_zh`, `brief`, `key_points`, `voice_intro`, `voice_points`
- every `brief` equals `voice_intro`
- `key_points` count stays in `4..6`
- `voice_points` count stays in `1..2`
- failed selected rows remain traceable and do not vanish silently from the final output

**Step 2: Run the single e2e test**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py::test_run_stage3_writes_expected_output
```

Expected: PASS.

**Step 3: Run full test file**

Run:
```bash
pytest -q tests/test_topic_stage3_generate_content.py
```

Expected: PASS.

---

## Review Checklist for the Implementer

Before calling stage 3 done, confirm:
- [ ] stage 3 reads `pool_stage2_final.jsonl` and does not redo stage-2 scoring/enrichment
- [ ] selection uses only existing stage-2 fields plus original input order
- [ ] selected ordering is deterministic
- [ ] stage 3 uses the simple PRL-like API-generation mode first, without prompt over-engineering
- [ ] `brief == voice_intro` in the stable output contract, and `brief` is copied rather than separately generated
- [ ] `key_points` contains 4~6 clean Chinese sentences
- [ ] `voice_points` contains 1~2 clean Chinese sentences
- [ ] failed papers are skipped individually from `papers`, recorded in `failed_papers`, and total failure raises a clear error
- [ ] tests use fake/mock request paths; no live network in unit tests

---

## Verification Commands

After implementation, run exactly:

```bash
pytest -q tests/test_topic_stage3_generate_content.py
python3 -m py_compile scripts/topic_stage3_generate_content.py
```

If a manual smoke run is needed:

```bash
cat >/tmp/topic_stage3_demo.json <<'JSON'
{
  "topic_query": "moire exciton twisted bilayer",
  "stage2_final": "/tmp/topic-stage2-demo/pool_stage2_final.jsonl",
  "outdir": "/tmp/topic-stage3-demo",
  "selected_n": 8,
  "llm_mode": "fake",
  "write_debug_artifacts": true
}
JSON

/usr/bin/python3 scripts/topic_stage3_generate_content.py --config /tmp/topic_stage3_demo.json
```

Expected outputs:
- `/tmp/topic-stage3-demo/input_stage3.json`
- optionally `/tmp/topic-stage3-demo/api_debug.jsonl`
- optionally `/tmp/topic-stage3-demo/stage3_selected_preview.json`

---

## Notes on Deliberate Simplifications

These simplifications are intentional for stage 3:
- no prompt optimization pass yet
- no extra score layer beyond stage 2 fields
- no re-ranking by API
- no combined mega-prompt for all papers
- no rendering or publish-generation in this stage
- `brief` and `voice_intro` intentionally share one value in v1, matching the current PRL pipeline

This keeps stage 3 close to the proven PRL path, easy to debug, and ready for later prompt refinement only after the simple baseline is working.
