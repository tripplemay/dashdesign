# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files


ROOT = Path.cwd()
IS_MACOS = sys.platform == "darwin"


def collect_datas():
    datas = []
    for directory in ("scripts", "tools"):
        path = ROOT / directory
        if path.exists():
            datas.append((str(path), directory))
    for file_name in ("requirements.txt", "requirements-desktop.txt", "VERSION", "UPDATE_MANIFEST_URL"):
        path = ROOT / file_name
        if path.exists():
            datas.append((str(path), "."))
    baseline_dir = ROOT / "docs" / "baseline"
    if baseline_dir.exists():
        for file_name in ("README.md", "baseline.schema.json", "baseline.v1.draft.json"):
            path = baseline_dir / file_name
            if path.exists():
                datas.append((str(path), "docs/baseline"))
    prompt_template_dir = ROOT / "docs" / "prompt_templates"
    if prompt_template_dir.exists():
        for path in prompt_template_dir.glob("*.json"):
            datas.append((str(path), "docs/prompt_templates"))
    return datas


COMMON_DATAS = collect_datas()
# 主题库（SVG）与图标库（字体）都有包内数据文件，且当前 hooks-contrib
# 没有对应 hook，必须显式收集，否则打包版启动即缺资源。
GUI_DATAS = (
    COMMON_DATAS
    + collect_data_files("qdarktheme")
    + collect_data_files("qtawesome")
)
GUI_BINARIES = []
GUI_HIDDEN_IMPORTS = [
    "qdarktheme",
    "qtawesome",
    "qtpy",
    "qtpy.QtCore",
    "qtpy.QtGui",
    "qtpy.QtWidgets",
    "jsonschema",
    "pypdfium2",
    "docx",
    # Phase B cloud client is lazy-imported in ui.baseline_service (only when a
    # cloud endpoint is configured), so PyInstaller's static analysis misses it.
    # It needs only `requests` (already bundled); the server package is excluded.
    "cloud",
    "cloud.client",
]
# 基线摄取/校验依赖：pypdfium2 带原生 pdfium 二进制、python-docx 带 default.docx
# 模板、jsonschema 需 metadata——用 collect_all 一次性收 datas/binaries/hiddenimports。
for _pkg in ("jsonschema", "pypdfium2", "docx"):
    _datas, _binaries, _hidden = collect_all(_pkg)
    GUI_DATAS += _datas
    GUI_BINARIES += _binaries
    GUI_HIDDEN_IMPORTS += _hidden
COMMON_HIDDEN_IMPORTS = [
    "argparse",
    "base64",
    "csv",
    "dataclasses",
    "datetime",
    "fnmatch",
    "gc",
    "hashlib",
    "json",
    "math",
    "re",
    "shutil",
    "stat",
    "subprocess",
    "textwrap",
    "typing",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
    "PIL.ImageEnhance",
    "PIL.ImageFilter",
    "PIL.ImageFont",
    "PIL.ImageOps",
    "cv2",
    "numpy",
    "qrcode",
    "requests",
    "urllib3",
]
COMMON_EXCLUDES = [
    "matplotlib",
    "pytest",
    "setuptools.tests",
    # Server-only backend must never be pulled into the desktop bundle.
    "cloud.server",
    "fastapi",
    "sqlalchemy",
    "uvicorn",
    "mangum",
]


gui_analysis = Analysis(
    ["desktop_qt_app.py"],
    pathex=[str(ROOT)],
    binaries=GUI_BINARIES,
    datas=GUI_DATAS,
    hiddenimports=COMMON_HIDDEN_IMPORTS + GUI_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=COMMON_EXCLUDES,
    noarchive=False,
    optimize=0,
)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="DashDesign",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

worker_analysis = Analysis(
    ["dashdesign_worker.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=COMMON_HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=COMMON_EXCLUDES,
    noarchive=False,
    optimize=0,
)
worker_pyz = PYZ(worker_analysis.pure)
worker_exe = EXE(
    worker_pyz,
    worker_analysis.scripts,
    [],
    exclude_binaries=True,
    name="DashDesignWorker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    gui_exe,
    worker_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    worker_analysis.binaries,
    worker_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DashDesign",
)

if IS_MACOS:
    app = BUNDLE(
        coll,
        name="DashDesign.app",
        icon=None,
        bundle_identifier="cn.dashdesign.app",
        info_plist={
            "CFBundleDisplayName": "DashDesign",
            "CFBundleName": "DashDesign",
            "NSHighResolutionCapable": True,
        },
    )
