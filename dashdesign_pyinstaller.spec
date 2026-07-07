# -*- mode: python ; coding: utf-8 -*-

from __future__ import annotations

import sys
from pathlib import Path


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
    return datas


COMMON_DATAS = collect_datas()
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
]


gui_analysis = Analysis(
    ["desktop_qt_app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=COMMON_DATAS,
    hiddenimports=COMMON_HIDDEN_IMPORTS,
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
