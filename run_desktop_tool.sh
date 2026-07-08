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

# 探测必须覆盖 Qt 客户端的全部硬依赖，缺一个都回退 Tk 客户端。
if "$PYTHON" - <<'PY' >/dev/null 2>&1
import PySide6.QtWidgets
import qtawesome
import qdarktheme
PY
then
  exec "$PYTHON" desktop_qt_app.py
fi

echo "PySide6/qtawesome/qdarktheme 不完整（请重新安装 requirements-desktop.txt），回退到 Tk 客户端。" >&2
exec "$PYTHON" desktop_tool.py
