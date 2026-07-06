#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
else
  PYTHON="python3"
fi

if [[ "${DASHDESIGN_DESKTOP_BACKEND:-qt}" == "tk" ]]; then
  exec "$PYTHON" desktop_tool.py
fi

if "$PYTHON" - <<'PY' >/dev/null 2>&1
import PySide6.QtWidgets
PY
then
  exec "$PYTHON" desktop_qt_app.py
fi

exec "$PYTHON" desktop_tool.py
