#!/usr/bin/env bash
# Copy this file to config/local.env.sh and fill in your own values.
# The pipeline entrypoint will source it automatically if present.

export PRL_LLM_MODE="api"
export OPENAI_API_KEY="your-api-key"
export OPENAI_BASE_URL="https://your-openai-compatible-endpoint/v1"
export OPENAI_MODEL="gpt-5.5"

# Local Bilibili login state used by biliup.
# Keep this file outside git and point to your own logininfo json.
export BILI_LOGIN_FILE="$HOME/work/hermes/workspace/bilibili/.secrets/bili_logininfo.json"

# Optional: explicitly point to biliup when cron/non-interactive shells do not inherit ~/.local/bin.
export BILIUP_BIN="$HOME/.local/bin/biliup"
