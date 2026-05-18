#!/usr/bin/env bash
set -euo pipefail

# Wrapper for biliup upload with defaults:
# - submit: web
# - tid: 171 (生活)
# - public by default
# Credentials are referenced by file path ONLY.

LOGIN_FILE="${BILI_LOGIN_FILE:-/home/cndaqiang/work/hermes/workspace/bilibili/.secrets/bili_logininfo.json}"
BILIUP_BIN="${BILIUP_BIN:-}"
if [[ -z "$BILIUP_BIN" ]]; then
  BILIUP_BIN="$(command -v biliup || true)"
fi

usage() {
  cat <<'EOF'
Usage:
  upload_web_171.sh --title "..." --desc "..." --tag "a,b" /path/to/video.mp4

Optional:
  --dynamic "..."
  --cover /path/to/cover.jpg
  --copyright 1|2   (default: 1)
  --source "..."     (required when copyright=2)
  --dtime <10-digit timestamp>

Env:
  BILI_LOGIN_FILE=...  Override login info json path
EOF
}

TITLE=""
DESC=""
TAG=""
DYNAMIC=""
COVER=""
COPYRIGHT="1"
SOURCE=""
DTIME=""

ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --title) TITLE="$2"; shift 2;;
    --desc) DESC="$2"; shift 2;;
    --tag) TAG="$2"; shift 2;;
    --dynamic) DYNAMIC="$2"; shift 2;;
    --cover) COVER="$2"; shift 2;;
    --copyright) COPYRIGHT="$2"; shift 2;;
    --source) SOURCE="$2"; shift 2;;
    --dtime) DTIME="$2"; shift 2;;
    -h|--help) usage; exit 0;;
    --) shift; break;;
    -*) echo "Unknown option: $1" >&2; usage; exit 2;;
    *) ARGS+=("$1"); shift;;
  esac
done

if [[ ${#ARGS[@]} -lt 1 ]]; then
  echo "Missing VIDEO_PATH" >&2
  usage
  exit 2
fi

VIDEO_PATH="${ARGS[0]}"

if [[ -z "$TITLE" ]]; then
  echo "Missing --title" >&2
  exit 2
fi

if [[ "$COPYRIGHT" == "2" && -z "$SOURCE" ]]; then
  echo "--source is required when --copyright 2" >&2
  exit 2
fi

if [[ -z "$BILIUP_BIN" || ! -x "$BILIUP_BIN" ]]; then
  echo "biliup not found. Set BILIUP_BIN or add biliup to PATH." >&2
  exit 127
fi

CMD=("$BILIUP_BIN" -u "$LOGIN_FILE" upload --submit web --tid 171 --copyright "$COPYRIGHT" --title "$TITLE")

[[ -n "$DESC" ]] && CMD+=(--desc "$DESC")
[[ -n "$TAG" ]] && CMD+=(--tag "$TAG")
[[ -n "$DYNAMIC" ]] && CMD+=(--dynamic "$DYNAMIC")
[[ -n "$COVER" ]] && CMD+=(--cover "$COVER")
[[ -n "$SOURCE" ]] && CMD+=(--source "$SOURCE")
[[ -n "$DTIME" ]] && CMD+=(--dtime "$DTIME")

CMD+=("$VIDEO_PATH")

# NOTE: must run in a PTY-capable environment.
exec "${CMD[@]}"
