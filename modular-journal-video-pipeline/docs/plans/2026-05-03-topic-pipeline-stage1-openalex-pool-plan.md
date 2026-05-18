# Topic Paper Pipeline Stage 1 Implementation Plan

> **For Hermes:** Focus only on stage 1 in this plan: OpenAlex retrieval, lightweight title-based dedup/merge, and pool file generation. Do not implement journal scoring, arXiv补摘要, or later ranking in this plan.

**Goal:** Build stage 1 of the topic-paper pipeline: query OpenAlex within a date window, save raw responses, normalize records, lightly deduplicate by title, and output a clean stage-1 candidate pool for later scoring/enrichment.

**Architecture:** Add a small standalone stage-1 builder under `scripts/` that reads a minimal config, fetches OpenAlex results, writes `openalex_raw.jsonl`, transforms them into a normalized in-memory record format, deduplicates by normalized title, and writes `pool_stage1.jsonl`. Keep stage 1 intentionally narrow and deterministic so later stages can consume it without hidden heuristics.

**Tech Stack:** Python 3, `requests`, JSONL files, existing repository `scripts/` conventions.

**Audit revision:** Keep this stage as one standalone script with pure helper functions. Do not introduce a class layer unless implementation reveals repeated mutable state that cannot be kept simple with functions.

---

## Scope Freeze

This plan covers only:
1. OpenAlex retrieval
2. Raw response persistence
3. Record normalization
4. Exact title-normalized dedup/merge
5. Stage-1 pool output

Explicitly out of scope:
- journal scoring tool
- arXiv or other source lookup
- LLM-based paper scoring
- fuzzy title matching
- paper-level ranking/filtering beyond the date-window OpenAlex query itself

---

## Proposed Files

### Create
- `scripts/topic_stage1_openalex_pool.py`
- `tests/test_topic_stage1_openalex_pool.py`
- `docs/plans/2026-05-03-topic-pipeline-stage1-openalex-pool-plan.md` (this file)

### Reuse / inspect but avoid modifying unless necessary
- `scripts/prepare_issue.py`
- `scripts/prl_llm_core.py`
- `templates/run_prl_daily_publish.sh`

---

## OpenAlex Query Contract

Use the narrowest practical query contract from day one:
- map `topic_query` directly to the OpenAlex `search` parameter
- do not add query expansion, synonym expansion, keyword rewriting, or multi-query fanout in stage 1
- filter by publication date window using `from_publication_date` and `to_publication_date`
- if OpenAlex exposes a stable and explicit work-type filter for journal articles, use that exact filter; otherwise do not invent fuzzy “journal-article-like” heuristics in the fetch layer
- if local type post-filtering is still needed, only allow an explicit whitelist of exact type values seen in the API response, and cover that whitelist with tests
- sort by `publication_date:desc` so the candidate pool is stable and newest-first
- page until `max_results` or exhaustion, whichever comes first
- after parsing, locally discard any record whose `publication_date` falls outside the requested window

This local post-filter is not “extra logic”; it is a guardrail against API quirks and keeps stage-1 outputs deterministic.

---

## Output Contract

Stage 1 should write two files under a caller-provided output directory:

### 1. `openalex_raw.jsonl`
One line per raw OpenAlex work record as returned by the fetch loop.

### 2. `pool_stage1.jsonl`
One line per normalized, lightly deduplicated record with this schema:

```json
{
  "record_id": "oa_000001",
  "source": "openalex",
  "openalex_id": "https://openalex.org/W123...",
  "topic_query": "moire excitons",
  "start_date": "2026-04-01",
  "end_date": "2026-05-01",
  "retrieved_at": "2026-05-03T12:00:00+08:00",
  "title": "...",
  "title_normalized": "...",
  "abstract": "",
  "publication_date": "2026-04-28",
  "journal": "Physical Review Letters",
  "doi": "10.1103/...",
  "paper_url": "https://doi.org/...",
  "authors": ["A", "B"],
  "first_author": "A",
  "raw_source_ids": ["https://openalex.org/W123..."],
  "raw_duplicate_count": 1
}
```

- `record_id`: sequential within one run, e.g. `oa_000001`
- `retrieved_at`: record the fetch timestamp in Asia/Shanghai offset form, e.g. `2026-05-03T12:00:00+08:00`
- `paper_url`: prefer DOI URL when DOI exists; otherwise fall back to the best OpenAlex landing URL available
- `raw_source_ids`: keep all merged OpenAlex work IDs in first-seen order without duplicates
- `raw_duplicate_count`: equal to the number of raw records merged into this pooled row
- `abstract` may be empty in stage 1.
- `authors` may be empty.
- `title_normalized` is for exact dedup only, not for display.

---

## Config Contract

Use a minimal JSON config for stage 1. Example:

```json
{
  "topic_query": "moire excitons in twisted bilayers",
  "start_date": "2026-04-01",
  "end_date": "2026-05-01",
  "max_results": 100,
  "outdir": "/tmp/topic-stage1-demo"
}
```

Do not add extra knobs yet.

---

## Title Normalization Contract

`title_normalized` must be deterministic and intentionally simple. Use this exact sequence:
1. Unicode normalize with NFKC
2. lowercase
3. trim leading/trailing whitespace
4. replace these separator-like punctuation characters with a single space: `-`, `‐`, `‑`, `‒`, `–`, `—`, `/`, `:`, `,`, `.`, `;`, `(`, `)`, `[`, `]`, `{`, `}`
5. collapse all repeated whitespace to a single space
6. return the final string

Hard boundaries:
- do not use fuzzy similarity
- do not use stemming or lemmatization
- do not map semantic equivalents
- do not use DOI or authors as a secondary dedup key in stage 1
- if normalized title is empty, drop that raw record from the pooled output and cover this with a test

The point of `title_normalized` is only exact equality after a tiny amount of normalization.

---

## Merge Contract for Exact-Title Duplicates

When two raw records share the same `title_normalized`, merge them with these fixed rules:
- `title`: keep the first-seen non-empty original title
- `title_normalized`: unchanged
- `openalex_id`: keep the first-seen record's `openalex_id`
- `topic_query`, `start_date`, `end_date`, `retrieved_at`, `source`: unchanged from the run context
- `abstract`: replace only if current value is empty and incoming value is non-empty
- `journal`: replace only if current value is empty and incoming value is non-empty
- `doi`: replace only if current value is empty and incoming value is non-empty
- `paper_url`: if final merged `doi` is non-empty, rebuild as `https://doi.org/<doi>`; otherwise keep the first non-empty URL seen
- `authors`: replace only if incoming author list is longer and non-empty
- `first_author`: always recompute from final `authors`; if `authors` is empty, use empty string
- `publication_date`: keep the first-seen non-empty value so ordering stays deterministic
- `raw_source_ids`: append unseen IDs in first-seen order
- `raw_duplicate_count`: increment by one per merged raw record

This contract is intentionally conservative: prefer stable first-seen values, but fill obvious blanks when a later duplicate is richer.

---

## Implementation Tasks

### Task 1: Create test file and lock the public contract

**Objective:** Define the expected behavior of the stage-1 builder before implementation.

**Files:**
- Create: `tests/test_topic_stage1_openalex_pool.py`

**Step 1: Write failing tests for pure helpers and merge behavior**

Include tests for:
- title normalization
- exact title-normalized dedup
- merge preference for richer fields
- normalized output record shape
- empty-title / empty-normalized-title drop behavior
- stable first-seen ordering after dedup

Suggested tests:

```python
from scripts.topic_stage1_openalex_pool import (
    normalize_title,
    merge_stage1_records,
    dedup_stage1_records,
)


def test_normalize_title_collapses_basic_punctuation_and_space():
    a = "Magnetic-Field-Driven Insulator-Superconductor Transition"
    b = "  magnetic field driven  insulator superconductor transition  "
    assert normalize_title(a) == normalize_title(b)


def test_merge_stage1_records_prefers_non_empty_fields():
    left = {
        "title": "Example Title",
        "title_normalized": "example title",
        "abstract": "",
        "journal": "",
        "doi": "",
        "authors": [],
        "first_author": "",
        "openalex_id": "W1",
        "raw_source_ids": ["W1"],
        "raw_duplicate_count": 1,
    }
    right = {
        "title": "Example Title",
        "title_normalized": "example title",
        "abstract": "Useful abstract",
        "journal": "PRL",
        "doi": "10.1/test",
        "authors": ["A Author"],
        "first_author": "A Author",
        "openalex_id": "W2",
        "raw_source_ids": ["W2"],
        "raw_duplicate_count": 1,
    }
    merged = merge_stage1_records(left, right)
    assert merged["abstract"] == "Useful abstract"
    assert merged["journal"] == "PRL"
    assert merged["doi"] == "10.1/test"
    assert merged["authors"] == ["A Author"]
    assert merged["raw_duplicate_count"] == 2
    assert merged["raw_source_ids"] == ["W1", "W2"]


def test_dedup_stage1_records_merges_exact_normalized_title_matches():
    records = [
        {"title": "A-B", "title_normalized": "ab", "raw_source_ids": ["W1"], "raw_duplicate_count": 1},
        {"title": "A B", "title_normalized": "ab", "raw_source_ids": ["W2"], "raw_duplicate_count": 1},
    ]
    out = dedup_stage1_records(records)
    assert len(out) == 1
    assert out[0]["raw_duplicate_count"] == 2
```

**Step 2: Run tests to verify failure**

Run:
```bash
pytest -q tests/test_topic_stage1_openalex_pool.py
```

Expected: FAIL — module/functions do not exist yet.

---

### Task 2: Create stage-1 script skeleton and pure helper functions

**Objective:** Implement the minimal pure functions required by tests.

**Files:**
- Create: `scripts/topic_stage1_openalex_pool.py`
- Test: `tests/test_topic_stage1_openalex_pool.py`

**Step 1: Add module skeleton**

Required functions in initial skeleton:
- `normalize_title(text: str) -> str`
- `pick_richer_value(left, right)`
- `merge_stage1_records(left: dict, right: dict) -> dict`
- `dedup_stage1_records(records: list[dict]) -> list[dict]`

**Step 2: Implement `normalize_title()`**

Rules:
- Unicode normalize NFKC
- lowercase
- trim
- replace the explicit separator set defined in the title-normalization contract with spaces
- collapse repeated whitespace
- keep implementation simple and deterministic

Do not add fuzzy matching, stemming, lemmatization, or semantic rewriting.

**Step 3: Implement merge logic**

Use these field rules:
- follow the exact merge contract defined above rather than ad-hoc “best effort” merging
- prefer non-empty `abstract` only when current value is empty
- prefer non-empty `journal` only when current value is empty
- prefer non-empty `doi` only when current value is empty
- prefer longer non-empty `authors`
- recompute `first_author` from final `authors`
- preserve first record ordering unless replacement is explicitly required by the merge contract
- concatenate unique `raw_source_ids`
- increment `raw_duplicate_count`

**Step 4: Implement exact dedup by `title_normalized`**

Use insertion-order-preserving dict logic.

**Step 5: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage1_openalex_pool.py
```

Expected: PASS for helper tests.

---

### Task 3: Add OpenAlex raw-record parsing tests

**Objective:** Lock down how one raw OpenAlex work becomes one normalized stage-1 record.

**Files:**
- Modify: `tests/test_topic_stage1_openalex_pool.py`
- Modify: `scripts/topic_stage1_openalex_pool.py`

**Step 1: Add a fixture-like raw OpenAlex sample in test**

Test should cover extraction of:
- `openalex_id`
- `title`
- reconstructed `abstract`
- `publication_date`
- `journal`
- `doi`
- `paper_url`
- `authors`
- `first_author`

**Step 2: Add test for abstract reconstruction**

OpenAlex may expose abstract via inverted index; test reconstruction into plain text.

**Step 3: Add test for local post-filter guards**

Cover at least:
- parsed `publication_date` outside the requested window is dropped before pooling
- records with empty normalized title are dropped before pooling
- if a local type whitelist is used, non-whitelisted exact type values are dropped

**Step 4: Add parser function**

Implement something like:
- `parse_openalex_work(work: dict, *, topic_query: str, start_date: str, end_date: str, retrieved_at: str) -> dict`

**Step 5: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage1_openalex_pool.py
```

Expected: PASS.

---

### Task 4: Add OpenAlex fetcher with pagination boundary

**Objective:** Fetch raw works from OpenAlex up to `max_results` and save them raw.

**Files:**
- Modify: `scripts/topic_stage1_openalex_pool.py`
- Modify: `tests/test_topic_stage1_openalex_pool.py`

**Step 1: Add a test for fetch-loop orchestration with mocked HTTP**

Test expectations:
- respects `max_results`
- stops when no more results
- yields raw work dicts in order

Use monkeypatch on `requests.get` rather than real network.

**Step 2: Implement fetch function**

Suggested function:
- `fetch_openalex_works(topic_query: str, start_date: str, end_date: str, max_results: int) -> list[dict]`

Requirements:
- small page size acceptable
- deterministic ordering with `publication_date:desc`
- explicit timeout
- raise on HTTP failure
- set a descriptive `User-Agent` header so later排查更容易
- keep fetch and local post-filter separate so API behavior and our business rule are independently testable

**Step 3: Implement raw JSONL writer**

Suggested function:
- `write_jsonl(path: Path, rows: list[dict]) -> None`

**Step 4: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage1_openalex_pool.py
```

Expected: PASS.

---

### Task 5: Add config loader and main pipeline entrypoint

**Objective:** Turn the helpers into a runnable stage-1 script.

**Files:**
- Modify: `scripts/topic_stage1_openalex_pool.py`
- Modify: `tests/test_topic_stage1_openalex_pool.py`

**Step 1: Add config loader test**

Input: minimal JSON config file.  
Expected: parsed fields, outdir created/validated.

Also add explicit failure cases for:
- `start_date > end_date`
- `max_results <= 0`
- missing required fields

**Step 2: Implement config loader**

Suggested function:
- `load_stage1_config(path: str | Path) -> dict`

Validate required fields:
- `topic_query`
- `start_date`
- `end_date`
- `max_results`
- `outdir`

**Step 3: Implement high-level builder**

Suggested function:
- `build_stage1_pool(config: dict) -> dict`

Responsibilities:
1. fetch raw works
2. write `openalex_raw.jsonl`
3. parse to normalized records
4. apply local guards: drop out-of-window records, drop empty-normalized-title records, and apply exact type whitelist if configured by the fetch contract
5. dedup
6. assign `record_id` sequentially in stable first-seen order after dedup
7. write `pool_stage1.jsonl`
8. return summary dict

**Step 4: Add CLI entrypoint**

Suggested CLI:
```bash
/usr/bin/python3 scripts/topic_stage1_openalex_pool.py --config /path/to/config.json
```

Print only concise summary, for example:
```text
raw=87
pool=74
outdir=/tmp/topic-stage1-demo
```

**Step 5: Run tests**

Run:
```bash
pytest -q tests/test_topic_stage1_openalex_pool.py
```

Expected: PASS.

---

### Task 6: Add end-to-end contract test for file outputs

**Objective:** Verify stage 1 produces both required files with correct shape.

**Files:**
- Modify: `tests/test_topic_stage1_openalex_pool.py`
- Modify: `scripts/topic_stage1_openalex_pool.py`

**Step 1: Add an e2e test with mocked OpenAlex response**

Expected outputs in temp dir:
- `openalex_raw.jsonl`
- `pool_stage1.jsonl`

Assertions:
- both files exist
- raw line count equals fetched result count
- pool line count equals deduped result count
- every pool row has required fields

**Step 2: Run the single e2e test**

Run:
```bash
pytest -q tests/test_topic_stage1_openalex_pool.py::test_build_stage1_pool_writes_expected_files
```

Expected: PASS.

**Step 3: Run full test file**

Run:
```bash
pytest -q tests/test_topic_stage1_openalex_pool.py
```

Expected: PASS.

---

## Suggested Internal Function Layout

Inside `scripts/topic_stage1_openalex_pool.py`, aim for this order:

1. imports / constants
2. `normalize_title`
3. abstract reconstruction helper
4. `pick_richer_value`
5. `merge_stage1_records`
6. `dedup_stage1_records`
7. `parse_openalex_work`
8. `fetch_openalex_works`
9. `write_jsonl`
10. `load_stage1_config`
11. `build_stage1_pool`
12. CLI `main()`

Keep HTTP, parsing, and dedup logic separated.

---

## Review Checklist for the Implementer

Before calling stage 1 done, confirm:
- [ ] Only OpenAlex is used
- [ ] `topic_query` maps directly to OpenAlex `search` with no query expansion
- [ ] No arXiv lookup was added
- [ ] No journal scoring logic was added
- [ ] Dedup uses exact normalized-title equality only
- [ ] Empty normalized titles are dropped before dedup
- [ ] Local date-window guard is applied after parsing
- [ ] Type filtering, if any, uses only an explicit exact-value whitelist covered by tests
- [ ] Raw and pool files are both written
- [ ] `pool_stage1.jsonl` includes all required fields
- [ ] `record_id` ordering is stable and first-seen-based after dedup
- [ ] Empty abstract is allowed
- [ ] Tests use mocking; no live network in unit tests

---

## Verification Commands

After implementation, run exactly:

```bash
pytest -q tests/test_topic_stage1_openalex_pool.py
python3 -m py_compile scripts/topic_stage1_openalex_pool.py
```

If a manual smoke run is needed:

```bash
cat >/tmp/topic_stage1_demo.json <<'JSON'
{
  "topic_query": "moire excitons in twisted bilayers",
  "start_date": "2026-04-01",
  "end_date": "2026-05-01",
  "max_results": 20,
  "outdir": "/tmp/topic-stage1-demo"
}
JSON

/usr/bin/python3 scripts/topic_stage1_openalex_pool.py --config /tmp/topic_stage1_demo.json
```

Expected outputs:
- `/tmp/topic-stage1-demo/openalex_raw.jsonl`
- `/tmp/topic-stage1-demo/pool_stage1.jsonl`

---

## Notes on Deliberate Simplifications

These simplifications are intentional for stage 1:
- no fuzzy title clustering
- no author-based hard filtering
- no cross-source merge logic
- no score fields in pool output
- no attempt to “fix” missing metadata beyond choosing the richer duplicate among exact-title matches

This keeps stage 1 cheap to reason about and easy to debug.
