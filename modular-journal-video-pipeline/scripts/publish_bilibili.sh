#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPLOAD_SCRIPT="${PRL_UPLOAD_SCRIPT:-${SCRIPT_DIR}/upload_web_171.sh}"
LOGIN_FILE="${BILI_LOGIN_FILE:-/home/cndaqiang/work/hermes/workspace/bilibili/.secrets/bili_logininfo.json}"

usage() {
  cat <<'EOF'
Usage:
  publish_bilibili.sh --video out.mp4 --cover cover.png --title "..." [--desc-file publish_desc.txt] [--tags-file publish_tags.txt]
EOF
}

VIDEO=""
COVER=""
TITLE=""
DESC_FILE=""
TAGS_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --video) VIDEO="$2"; shift 2 ;;
    --cover) COVER="$2"; shift 2 ;;
    --title) TITLE="$2"; shift 2 ;;
    --desc-file) DESC_FILE="$2"; shift 2 ;;
    --tags-file) TAGS_FILE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[[ -n "$VIDEO" && -f "$VIDEO" ]] || { echo "Missing --video file" >&2; exit 1; }
[[ -n "$COVER" && -f "$COVER" ]] || { echo "Missing --cover file" >&2; exit 1; }
[[ -n "$TITLE" ]] || { echo "Missing --title" >&2; exit 1; }
[[ -f "$UPLOAD_SCRIPT" ]] || { echo "Missing upload script: $UPLOAD_SCRIPT" >&2; exit 1; }

DESC=""
[[ -n "$DESC_FILE" && -f "$DESC_FILE" ]] && DESC="$(cat "$DESC_FILE")"

TAG_CSV=""
if [[ -n "$TAGS_FILE" && -f "$TAGS_FILE" ]]; then
  TAG_CSV="$(tr '\n' ' ' < "$TAGS_FILE" | sed 's/#//g' | sed 's/[[:space:]]\+/,/g' | sed 's/^,*//; s/,*$//; s/,,*/,/g')"
fi

BILI_LOGIN_FILE="$LOGIN_FILE" \
"$UPLOAD_SCRIPT" \
  --title "$TITLE" \
  --desc "$DESC" \
  --tag "$TAG_CSV" \
  --cover "$COVER" \
  "$VIDEO"
