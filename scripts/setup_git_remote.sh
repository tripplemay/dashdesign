#!/usr/bin/env bash
set -euo pipefail

REMOTE_URL="${1:-}"
REMOTE_NAME="${2:-origin}"

if [[ -z "$REMOTE_URL" ]]; then
  echo "Usage: $0 git@github.com:ORG/REPO.git [remote-name]" >&2
  exit 2
fi

if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  git remote set-url "$REMOTE_NAME" "$REMOTE_URL"
else
  git remote add "$REMOTE_NAME" "$REMOTE_URL"
fi

git remote -v
