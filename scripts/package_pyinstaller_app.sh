#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

"$PYTHON" -m pip install -r requirements.txt -r requirements-desktop.txt -r requirements-packaging.txt
./scripts/bootstrap_runtime_assets.sh

rm -rf build dist/DashDesign dist/DashDesign.app

exec "$PYTHON" -m PyInstaller \
  --clean \
  --noconfirm \
  dashdesign_pyinstaller.spec
