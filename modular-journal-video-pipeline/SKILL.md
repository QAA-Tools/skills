---
name: modular-journal-video-pipeline
description: "Design journal/news/research video pipelines as four separable layers: source prep, LLM JSON enrichment, JSON-driven rendering, and publishing. Keep orchestration thin so sources and templates can be swapped independently."
homepage: https://github.com/QAA-Tools/skills
metadata:
  clawdbot:
    emoji: "🧩"
---

# Modular Journal Video Pipeline

Use this skill when designing or refactoring a content-to-video pipeline that may later support:
- different journals or feeds
- different LLM prompts/models
- different video templates
- different publishing targets

## Core rule

Do **not** build one giant Python script that fetches data, enriches it with LLMs, renders video, and uploads it.

Instead, split the system into **four layers** with explicit file contracts:

1. **Source Prep**
2. **LLM Enrichment**
3. **Rendering**
4. **Publishing**

A thin Bash orchestrator may call these layers in order, but should contain almost no business logic.

---

## Architecture

### Layer 1: Source Prep

**Responsibility:** acquire, normalize, and filter source material.

Typical tasks:
- fetch RSS/API/web data
- resolve abstracts / summaries from secondary sources
- deduplicate items
- rank or filter candidates
- emit deterministic machine-friendly JSON

**Input:** source configuration only

**Output:** `raw.json`

**Must not do:**
- LLM phrasing
- video wording
- rendering
- uploading

### Layer 2: LLM Enrichment

**Responsibility:** take prepared structured records and add presentation-oriented JSON fields.

Typical tasks:
- generate `brief`
- generate `key_points`
- optionally generate translated titles
- optionally generate publish description and tags
- preserve source facts without changing selection semantics

**Input:** `raw.json`

**Output:** `input.json`

**Must not do:**
- source fetching
- abstract crawling strategy
- rendering
- uploading

### Layer 3: Rendering

**Responsibility:** render assets purely from JSON.

Typical tasks:
- render slides/cards
- create audio/TTS
- compose `out.mp4`
- generate cover images
- emit render metadata

**Input:** `input.json`

**Output:** `out.mp4`, `cover.png`, `script.txt`, `meta.json`, slide images, audio cache

**Must not do:**
- source fetching
- article filtering
- LLM generation
- uploading

### Layer 4: Publishing

**Responsibility:** publish already-built assets.

Typical tasks:
- upload video
- upload cover
- apply title/description/tags
- return publish receipt such as BVID/AID or platform IDs

**Input:** rendered assets + publish metadata

**Output:** publish receipt / logs

**Must not do:**
- source fetching
- LLM generation
- rendering

---

## Recommended file layout

```text
project/
  scripts/
    prepare_issue.py          # Layer 1
    enrich_issue_llm.py       # Layer 2
    render_issue.py           # Layer 3
    publish_bilibili.sh       # Layer 4
    run_issue_pipeline.sh     # thin orchestration only
  schemas/
    raw.schema.json
    input.schema.json
  examples/
    raw.example.json
    input.example.json
  output/
    YYYY-MM-DD-issue/
      raw.json
      input.json
      out.mp4
      cover.png
      publish_desc.txt
      publish_tags.txt
      meta.json
```

---

## Contract design

### `raw.json`

`raw.json` should represent source truth and selection results, not presentation polish.

Recommended contents:
- issue date / run date
- source metadata
- feed/article dates
- selected items
- per-item factual fields such as title, DOI, abstract, URL, ranking score, source group
- optional debugging flags for extraction quality

Example shape:

```json
{
  "date": "2026-04-30",
  "source": "APS PRL condensed matter RSS",
  "feed_date": "2026-04-28",
  "items": [
    {
      "title_en": "...",
      "doi": "10.1103/...",
      "abstract_en": "...",
      "url": "...",
      "score": 17.5,
      "source_group": "condensed"
    }
  ]
}
```

### `input.json`

`input.json` should keep the selected records but add presentation fields for downstream renderers.

Recommended contents:
- outward-facing local issue date
- cover title/subtitle
- section labels
- papers with factual fields plus presentation fields
- publish description/tags if desired

Example shape:

```json
{
  "date": "2026-04-30",
  "video_title": "PRL今日热点",
  "cover_title": "PRL今日热点",
  "cover_subtitle": "2026-04-30 · 今日热点",
  "papers": [
    {
      "title_en": "...",
      "title_zh": "...",
      "doi": "10.1103/...",
      "brief": "...",
      "key_points": ["...", "..."]
    }
  ]
}
```

---

## Why this split matters

### 1. Swap source logic without touching render

If you later change from PRL RSS to Nature, Science, arXiv, or a custom database, only Layer 1 should change.

### 2. Swap templates without touching source logic

If you later want a new visual template, vertical layout, horizontal YouTube layout, or a static-image carousel, only Layer 3 should change.

### 3. Reuse the same prepared issue for multiple outputs

The same `input.json` can drive:
- Bilibili video
- YouTube video
- image carousel
- newsletter summary
- website article page

### 4. Debug by layer

When something breaks, the failure domain stays obvious:
- missing facts -> Layer 1
- bad wording -> Layer 2
- ugly layout -> Layer 3
- platform/API failure -> Layer 4

---

## Thin orchestration rule

Use a small Bash script as the top-level entrypoint.

Good responsibilities for `run_issue_pipeline.sh`:
- source environment
- choose output directory
- call each layer in order
- stop on failure
- print paths and publish receipt

Bad responsibilities for `run_issue_pipeline.sh`:
- custom parsing logic
- JSON mutation with ad-hoc regex
- template logic
- ranking logic
- platform-specific retry state machines beyond simple shell control flow

Example shape:

```bash
#!/usr/bin/env bash
set -euo pipefail

OUTDIR="${1:-$PWD/output/$(date +%F-%H%M%S)}"
mkdir -p "$OUTDIR"

/usr/bin/python3 scripts/prepare_issue.py --out "$OUTDIR/raw.json"
/usr/bin/python3 scripts/enrich_issue_llm.py --raw "$OUTDIR/raw.json" --out "$OUTDIR/input.json"
/usr/bin/python3 scripts/render_issue.py --input "$OUTDIR/input.json" --outdir "$OUTDIR"
scripts/publish_bilibili.sh \
  --video "$OUTDIR/out.mp4" \
  --cover "$OUTDIR/cover.png" \
  --desc-file "$OUTDIR/publish_desc.txt" \
  --tags-file "$OUTDIR/publish_tags.txt"
```

---

## Refactor guidance

When converting an existing monolithic script, move logic in this order:

1. Extract source acquisition + filtering into Layer 1.
2. Make Layer 1 emit stable `raw.json`.
3. Extract prompt generation + JSON fill into Layer 2.
4. Make Layer 3 consume only `input.json`.
5. Keep publishing as a separate final step.
6. Only after the four layers exist, add a thin Bash entrypoint.

Do **not** start by writing a new giant orchestrator.

---

## Anti-patterns

Avoid these:
- one Python file owning fetching, ranking, LLM calls, rendering, and upload
- renderers that secretly fetch missing source data
- LLM steps that decide source selection implicitly
- upload scripts that mutate content JSON
- tightly coupling one journal source to one visual template
- a "new" pipeline whose wrappers still import or shell out to legacy project paths outside the new skill/repo

---

## Reusable migration pattern

When splitting a legacy pipeline, the safe reusable path is:

1. identify the minimal legacy modules/scripts each layer actually needs
2. copy or move those implementations into the new skill/repo first
3. retarget all wrappers to local files/modules only
4. run a repo-wide search for legacy absolute paths before claiming the split is done
5. only then start smaller cleanup/refactors inside the new local copies

This is still a valid split-first migration, but it only counts as independent once the new pipeline no longer depends on external legacy paths.

### Independence check

Before saying the new skill is complete, verify all of the following:
- searching the new repo for legacy absolute paths returns zero matches
- `prepare_issue.py` imports only local modules
- `enrich_issue_llm.py` imports only local modules
- `render_issue.py` invokes only local renderer scripts
- `publish_bilibili.sh` uses a local uploader wrapper/script by default

---

## Acceptance checklist

A pipeline follows this skill when:
- source replacement does not require renderer rewrite
- template replacement does not require source rewrite
- `raw.json` exists before LLM enrichment
- `input.json` exists before rendering
- rendering consumes JSON only
- publishing consumes assets only
- top-level orchestration is thin and replaceable

---

## Runtime dependency note

Keep runtime setup brief and externalized:
- LLM/API config should come from environment variables such as `OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL`.
- Publishing credentials should come from local secret files or env vars such as `BILI_LOGIN_FILE`, never from tracked repository files.
- For this pipeline repo, keep local credentials in `config/local.env.sh` and keep a tracked template in `config/local.env.example.sh`.
- The top-level runner `templates/run_prl_daily_publish.sh` auto-sources `config/local.env.sh` when present; there should be no dependency on `/tmp/...` host-specific bootstrap files.
- Python, ffmpeg, TTS libraries, and uploader CLIs belong to implementation/runtime docs, not to the architecture contract itself.

## Preferred outcome

The resulting system should let you independently change:
- **where data comes from**
- **how text is generated**
- **how video looks**
- **where assets are published**

without collapsing maintainability into one all-in-one script.
