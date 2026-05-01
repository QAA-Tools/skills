# skills

This repository stores reusable skill directories.

## Metadata policy

`SKILL.md` is the source of truth; JSON metadata files are optional.

## Skills in this repository

### `modular-journal-video-pipeline`

Design journal/news/research video pipelines as four separable layers: source prep, LLM JSON enrichment, JSON-driven rendering, and publishing. Keep orchestration thin so sources and templates can be swapped independently.

### `ollama-web-search`

Web search via Ollama API. Returns relevant results from Ollama web search for AI agents.

### `py-env-setup`

Host-specific Python execution guidance for OpenClaw on this machine. Prefer `$PYTHON` over `python`/`python3` in PATH because non-interactive shells may not inherit the interactive shell environment.
