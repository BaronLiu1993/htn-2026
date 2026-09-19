#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
umask 077

printf 'Federato client ID (input hidden): '
IFS= read -r -s FEDERATO_ID_INPUT
printf '\nFederato client secret (input hidden): '
IFS= read -r -s FEDERATO_SECRET_INPUT
printf '\n'

if [[ -z "$FEDERATO_ID_INPUT" || -z "$FEDERATO_SECRET_INPUT" ]]; then
  printf 'Both values are required; .env was not changed.\n' >&2
  exit 2
fi

ENV_TEMP_FILE="$(mktemp "${TMPDIR:-/tmp}/underwriteiq-env.XXXXXX")"
trap 'rm -f "$ENV_TEMP_FILE"' EXIT

FOUND_ID=0
FOUND_SECRET=0
if [[ -f .env ]]; then
  while IFS= read -r LINE || [[ -n "$LINE" ]]; do
    case "$LINE" in
      FEDERATO_CLIENT_ID=*)
        printf 'FEDERATO_CLIENT_ID=%s\n' "$FEDERATO_ID_INPUT" >> "$ENV_TEMP_FILE"
        FOUND_ID=1
        ;;
      FEDERATO_CLIENT_SECRET=*)
        printf 'FEDERATO_CLIENT_SECRET=%s\n' "$FEDERATO_SECRET_INPUT" >> "$ENV_TEMP_FILE"
        FOUND_SECRET=1
        ;;
      *) printf '%s\n' "$LINE" >> "$ENV_TEMP_FILE" ;;
    esac
  done < .env
fi

if [[ "$FOUND_ID" -eq 0 ]]; then
  printf 'FEDERATO_CLIENT_ID=%s\n' "$FEDERATO_ID_INPUT" >> "$ENV_TEMP_FILE"
fi
if [[ "$FOUND_SECRET" -eq 0 ]]; then
  printf 'FEDERATO_CLIENT_SECRET=%s\n' "$FEDERATO_SECRET_INPUT" >> "$ENV_TEMP_FILE"
fi

mv "$ENV_TEMP_FILE" .env
trap - EXIT
unset FEDERATO_ID_INPUT FEDERATO_SECRET_INPUT
printf 'Federato credentials saved to ignored .env with owner-only permissions.\n'
