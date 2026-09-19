#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
read -r -s -p "OpenAI API key (input hidden): " OPENAI_API_KEY
printf '\n'
export OPENAI_API_KEY
python3 scripts/smoke_openai_agent.py
unset OPENAI_API_KEY
