#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" -m pip install -r requirements.txt -r requirements-desktop.txt
./scripts/bootstrap_runtime_assets.sh

DEPLOY_BIN="${PYSIDE6_DEPLOY:-}"
if [[ -z "$DEPLOY_BIN" ]]; then
  DEPLOY_BIN="$(command -v pyside6-deploy || command -v pyside6-deploy.exe || true)"
fi
if [[ -z "$DEPLOY_BIN" && -x ".venv/bin/pyside6-deploy" ]]; then
  DEPLOY_BIN=".venv/bin/pyside6-deploy"
fi
if [[ -z "$DEPLOY_BIN" ]]; then
  echo "pyside6-deploy not found. Install requirements-desktop.txt first." >&2
  exit 1
fi

exec "$DEPLOY_BIN" desktop_qt_app.py \
  --config-file pysidedeploy.spec \
  --name DashDesign \
  --force \
  --mode standalone \
  --extra-ignore-dirs=quality_eval,print_ready_200dpi,print_ready_v3_style_preserved_no_qr_rebuild_200dpi,print_samples_v3_style_preserved_200dpi,workflow_samples,single_no_qr_200dpi
