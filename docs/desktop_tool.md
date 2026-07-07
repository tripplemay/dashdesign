# DashDesign Desktop Tool

This desktop tool wraps the validated print workflows in a local GUI.

The primary client is now `desktop_qt_app.py`, built with PySide6 / Qt. The
legacy Tkinter tool remains as a fallback in `desktop_tool.py`.

Launch:

```bash
./run_desktop_tool.sh
```

The launcher starts the Qt client when PySide6 is available. To force the Tk
fallback:

```bash
DASHDESIGN_DESKTOP_BACKEND=tk ./run_desktop_tool.sh
```

Install desktop dependencies:

```bash
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-desktop.txt
./scripts/bootstrap_runtime_assets.sh
```

## Workflows

### 1. Project Baseline

Reads `docs/baseline/baseline.v1.draft.json` and shows the current to-C parent
and student poster baseline. This page is read-only for now. It is the local
preview surface for the future cloud-synchronized baseline service and
text-to-image prompt injection.

### 2. Text-to-Image

Calls `scripts/text_to_image_print.py`.

Use this when you want to generate a new poster background from text while
automatically injecting the current project baseline. The workflow uses only the
to-C parent/student baseline fields, visual guidelines, and prompt policy. It
does not inject the raw to-B partnership terms from the source documents.

Two output types are available:

- No-text background: the image model generates only the background/master art.
  Final title, course copy, logo, phone number, QR code, and price remain
  outside the model output.
- Poster with copy: the image model still generates only the background layer,
  then DashDesign composes the supplied Chinese poster copy locally. This avoids
  model-made Chinese typos and keeps print text deterministic.

Poster with copy also has a local text style selector:

- Clean education: clearer enrollment poster typography, white panels, and
  high readability.
- Tech neon: glowing AI-style headline, neon module cards, and a stronger
  promotional visual tone.

If `Execute API` is not checked, the tool creates an offline package containing
the final prompt, baseline context, request JSON, and generation record. If
checked, `OPENAI_API_KEY` is passed only as an environment variable for that
process and is not written to project files. When print post-processing is
enabled, the generated master is resized to the requested centimeter size and
DPI.

Keep the visual prompt and poster copy separate. The visual prompt should
describe scene, subject, mood, layout, and safe areas. The poster copy field can
accept pasted text such as `主标题：...`, `副标题：...`, `课程类型：...`,
and `结语：...`. The tool writes copy warnings into the output package when it
detects suspicious typos or when a QR code is only drawn as a non-scannable
placeholder.

### 3. Batch Print

Two modes are available:

- Style-preserved high-definition output: calls
  `scripts/batch_style_preserved_print.py`.
- Basic 200dpi output: calls `scripts/prepare_print_assets.py`.

The style-preserved mode keeps the original typography, generated text effects,
logo positions, and QR-code positions. It does not rebuild text or QR codes.

The basic mode is the stable PIL/Lanczos fallback. It is useful when
Real-ESRGAN is not needed or when you want the simplest deterministic output.

### 4. GPT Rebuild

Calls `scripts/gpt_image_rebuild.py`.

Use this when you want a GPT Image generation/edit request package for a source
poster. If `Execute API` is not checked, the tool only creates the request
package. If checked, `OPENAI_API_KEY` is passed only as an environment variable
for that process and is not written to project files.

### 5. Remove QR Area

Calls `scripts/remove_qr_area.py`.

This removes a manually specified area from one image and leaves clean artwork
space for adding a QR code later. It does not decode, regenerate, or move QR
codes.

The removal box format is:

```text
x1,y1,x2,y2
```

If `reference size` is empty, the box is interpreted in the input image's pixel
coordinates. If a reference size such as `3238x1295` is supplied, the box is
scaled to the actual input image size. This is useful when selecting a QR region
from the original low-resolution source but applying the cleanup to a 200dpi
output.

## Current Tooling

- Primary GUI: `desktop_qt_app.py`
- Fallback GUI: `desktop_tool.py`
- Launcher: `run_desktop_tool.sh`
- Real-ESRGAN binary: `tools/realesrgan-ncnn-vulkan`
- Real-ESRGAN models: `tools/models/`
- EDSR evaluation model: `tools/sr_models/EDSR_x4.pb`

## Qt Client Features

- Native app menu entries for opening the project/output directory, running,
  stopping, and quitting.
- Sidebar workflow navigation with separate parameter panels.
- Integrated image preview with fit, 100%, zoom in, zoom out, wheel zoom, and
  drag-and-drop.
- `QProcess` execution so the UI remains responsive while local workflows run.
- Image API keys are passed only as process environment variables and are not
  written to project files.

## Packaging

The current packaging entrypoint is:

```bash
./scripts/package_pyinstaller_app.sh
```

It uses PyInstaller and builds a standalone desktop app for the current
platform. On macOS the expected artifact is a `.app`; on Windows the expected
artifact is a packaged executable directory consumed by the Inno Setup
installer.

`scripts/package_qt_app.sh` and `pysidedeploy.spec` remain in the repository as
an experimental PySide Deploy/Nuitka path, but CI/CD uses PyInstaller because it
is faster and easier to observe in GitHub Actions for this project.

Release engineering scripts are available for:

- macOS signing/notarization: `packaging/macos/sign_notarize.sh`
- macOS DMG creation: `packaging/macos/create_dmg.sh`
- Windows signing: `packaging/windows/sign.ps1`
- Windows installer creation: `packaging/windows/build_installer.ps1`
- update manifest generation: `scripts/generate_update_manifest.py`

See `docs/release_engineering.md` for CI/CD, secrets, signing, notarization,
installer, and update-channel setup.

## Notes

- The GUI runs local scripts and streams their logs.
- Output directories are never deleted automatically.
- Existing images are not modified in place; workflows write to selected output
  directories.
- File names should include physical dimensions such as `200乘以80`.
