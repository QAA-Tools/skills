#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
MODULE_SCRIPTS_DIR="$ROOT_DIR/scripts"
PYTHON_BIN="${PRL_PYTHON_BIN:-/usr/bin/python3}"
ENV_FILE="${PRL_ENV_FILE:-$ROOT_DIR/config/local.env.sh}"
OUTPUT_BASE="${PRL_OUTPUT_BASE:-$HOME/work/hermes/workspace/bilibili/tmp/prl_daily}"
RUN_PREFIX="${PRL_RUN_PREFIX:-bashrun}"
ISSUE_DATE="$(date +%F)"
OUTDIR="${PRL_OUTDIR:-$OUTPUT_BASE/${RUN_PREFIX}-${ISSUE_DATE}-$(date +%H%M%S)}"

TITLE="${PRL_VIDEO_TITLE:-PRL今日热点 ${ISSUE_DATE}}"
FEED_N="${PRL_FEED_N:-25}"
N_PAPERS="${PRL_N_PAPERS:-8}"
DAYS_AGO="${PRL_DAYS_AGO:-0}"
AUTO_UPLOAD="${PRL_AUTO_UPLOAD:-0}"

mkdir -p "$OUTDIR"

if [[ ! -d "$MODULE_SCRIPTS_DIR" ]]; then
  echo "Missing module scripts dir: $MODULE_SCRIPTS_DIR" >&2
  exit 1
fi
if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Python not executable: $PYTHON_BIN" >&2
  exit 1
fi

if [[ -f "$ENV_FILE" ]]; then
  set +u
  source "$ENV_FILE"
  set -u
fi
export PRL_LLM_MODE="${PRL_LLM_MODE:-api}"

chmod +x "$MODULE_SCRIPTS_DIR/publish_bilibili.sh"
"$PYTHON_BIN" -m py_compile \
  "$MODULE_SCRIPTS_DIR/prepare_issue.py" \
  "$MODULE_SCRIPTS_DIR/enrich_issue_llm.py" \
  "$MODULE_SCRIPTS_DIR/render_issue.py"

RAW_JSON="$OUTDIR/raw.json"
INPUT_JSON="$OUTDIR/input.json"
PROMPT_TXT="$OUTDIR/llm_prompt.txt"
DESC_FILE="$OUTDIR/publish_desc.txt"
TAGS_FILE="$OUTDIR/publish_tags.txt"
VIDEO_FILE="$OUTDIR/out.mp4"
COVER_FILE="$OUTDIR/cover.png"

"$PYTHON_BIN" "$MODULE_SCRIPTS_DIR/prepare_issue.py" \
  --n "$N_PAPERS" \
  --feed-n "$FEED_N" \
  --days-ago "$DAYS_AGO" \
  --out "$RAW_JSON"

"$PYTHON_BIN" "$MODULE_SCRIPTS_DIR/enrich_issue_llm.py" \
  --raw "$RAW_JSON" \
  --out "$INPUT_JSON" \
  --prompt-out "$PROMPT_TXT" \
  --desc-out "$DESC_FILE" \
  --tags-out "$TAGS_FILE" \
  --selected-n "$N_PAPERS"

"$PYTHON_BIN" "$MODULE_SCRIPTS_DIR/render_issue.py" \
  --input "$INPUT_JSON" \
  --outdir "$OUTDIR" \
  --cover-out "$COVER_FILE" \
  --tags-file "$TAGS_FILE"

if [[ "$AUTO_UPLOAD" == "1" ]]; then
  "$MODULE_SCRIPTS_DIR/publish_bilibili.sh" \
    --video "$VIDEO_FILE" \
    --cover "$COVER_FILE" \
    --title "$TITLE" \
    --desc-file "$DESC_FILE" \
    --tags-file "$TAGS_FILE"
fi

TAG_CSV=""
if [[ -f "$TAGS_FILE" ]]; then
  TAG_CSV="$(tr -d '\n' < "$TAGS_FILE" | sed 's/^,*//; s/,*$//; s/,,*/,/g')"
fi

printf 'OUTDIR=%s\n' "$OUTDIR"
printf 'RAW_JSON=%s\n' "$RAW_JSON"
printf 'INPUT_JSON=%s\n' "$INPUT_JSON"
printf 'VIDEO=%s\n' "$VIDEO_FILE"
printf 'COVER=%s\n' "$COVER_FILE"
printf 'DESC_FILE=%s\n' "$DESC_FILE"
printf 'TAGS_FILE=%s\n' "$TAGS_FILE"
printf 'ENV_FILE=%s\n' "$ENV_FILE"
printf 'TITLE=%s\n' "$TITLE"
printf 'TAG_CSV=%s\n' "$TAG_CSV"
printf 'AUTO_UPLOAD=%s\n' "$AUTO_UPLOAD"
