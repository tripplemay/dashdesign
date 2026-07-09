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

Multi-project baseline manager. Baselines live in a per-user store under the
AppData dir (`baselines/<baseline_id>/versions/<version>.json`, append-only),
seeded on first run from the bundled `docs/baseline/baseline.v1.draft.json`.
The page has a project selector + version selector; the structured preview is
read-only. Version lifecycle runs through the Qt-free repository so schema
validation and B->C governance are enforced in one place:

- 设为活跃: pick which version generation uses (per project).
- 新建草稿: derive an append-only draft from the selected version (links
  parent_version; never mutates a published version).
- 校验: JSON-schema + governance (blocked-keyword / claims) check.
- 发布: draft -> published (immutable) + active; gated by validation and
  governance.
- 上传文档合并: upload a new intro/background doc (PDF/DOCX/TXT); the app parses
  it locally, runs a citation-grounded LLM extraction via the configured API,
  and shows a field-level review table. Blocked-keyword / suspected-promise /
  low-confidence rows are pre-unchecked and highlighted; B-side facts are
  bucketed into `source_facts.business_terms` (provenance only). Approving
  builds a new draft — nothing is auto-published.

The workflow generation always injects the active project's active baseline
version (resolved by `ui.baseline_service`). This is Phase A (local); a Phase-B
cloud backend will implement the same repository surface for multi-user shared
projects (see `docs/baseline/CLOUD_API.md`).

### 2. Text-to-Image

Calls `scripts/text_to_image_print.py` for background/local-composition modes
and `scripts/full_poster_image2.py` for complete-poster Image2 mode.

Use this when you want to generate a new poster background from text while
automatically injecting the current project baseline. The workflow uses only the
to-C parent/student baseline fields, visual guidelines, and prompt policy. It
does not inject the raw to-B partnership terms from the source documents.

Two output types are available:

- No-text background: the image model generates only the background/master art.
  Final title, course copy, logo, phone number, QR code, and price remain
  outside the model output.
- Complete poster Image2: the image model generates the full poster, including
  background, Chinese headline lettering, module badges, CTA, and QR placeholder
  area. This is the preferred exploration path when visual poster typography is
  more important than deterministic text-layer editing.

> Deprecated (2026-07): the "poster with copy" mode (background generation plus
> local Chinese typography composition) produced unsatisfactory results and has
> been removed from the desktop client. `scripts/text_to_image_print.py --mode
> poster` still exists for command-line experiments, but the GUI no longer
> exposes it.

Complete poster Image2 uses two additional controls:

- Purpose template: chooses the business goal such as enrollment, trial class,
  assessment booking, or course system introduction.
- Style template: chooses a professional visual direction such as tech neon,
  bright education, fantasy AI art, premium minimal, or comic pop.
- Layout template: controls poster composition such as headline/modules/CTA,
  central subject, portrait standee, or square social visual.
- Text density: tells the prompt compiler whether to keep text low, medium, or
  dense. Dense text increases review risk.
- Full-poster style: describes the overall art direction, typography style, and
  commercial poster tone sent to the image model. This is optional and is used
  as an extra requirement on top of the selected templates.
- Candidates: how many complete poster variants to generate. Use 3-4 for real
  selection; use 1 for a quick API smoke test.

The desktop client always calls the image API (there is no offline toggle;
the `立即调用 API` checkbox was removed). It uses the API base URL and key from
文件 → 设置 (see API Settings below); if none is configured it refuses to
run and points you there. When print post-processing is enabled, the generated
master is upscaled to the requested centimeter size and DPI using **Real-ESRGAN
x4** super-resolution (the same engine as batch style-preserved output),
falling back to PIL/Lanczos only if the Real-ESRGAN binary is unavailable or the
master is already large. (The underlying `scripts/text_to_image_print.py` still
supports an offline `--execute`-less run for command-line use.)

Keep the visual prompt and poster copy separate. The visual prompt should
describe scene, subject, mood, layout, and safe areas. The poster copy field can
accept pasted text such as `主标题：...`, `副标题：...`, `课程类型：...`,
and `结语：...`. The tool writes copy warnings into the output package when it
detects suspicious typos or when a QR code is only drawn as a non-scannable
placeholder.

In complete poster Image2 mode, the visual prompt is optional. When it is empty,
DashDesign compiles a professional prompt from the selected templates, current
project baseline, and poster copy.

For complete poster Image2, always compare generated candidates with
`expected_text.json` in the output package. Reject candidates with missing,
wrong, duplicated, rewritten, or extra readable text. Add the real scannable QR
code only after the poster image is approved.

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
poster. The desktop client always calls the API using the credentials from
文件 → 设置; the underlying `scripts/gpt_image_rebuild.py` still supports an
offline (`--execute`-less) request-package run for command-line use.

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

The Qt client can also select the removal box visually: click
`在预览图上框选`, then drag a rectangle on the preview image. The box
coordinates and the reference size are filled in automatically (coordinates are
mapped back to the original image pixels even when the preview shows a
downsampled copy).

## Current Tooling

- Primary GUI: `desktop_qt_app.py`
- Fallback GUI: `desktop_tool.py`
- Launcher: `run_desktop_tool.sh`
- Real-ESRGAN binary: `tools/realesrgan-ncnn-vulkan`
- Real-ESRGAN models: `tools/models/`
- EDSR evaluation model: `tools/sr_models/EDSR_x4.pb`

## Qt Client Features

- Native app menu entries for opening the project/output directory, running,
  stopping, and quitting (`Ctrl+R` / `Cmd+R` to run, `Ctrl+.` / `Cmd+.` to
  stop).
- Sidebar workflow navigation (with icons) and per-workflow parameter panels;
  mode-specific controls are shown or hidden per selected mode.
- Token-based theme system with light/dark variants; switch follow-system /
  light / dark from 文件 → 设置 (外观 section, with live preview) or the quick
  `视图 → 外观` menu (persisted via QSettings; the two stay in sync).
- Inline banner notifications for parameter errors, run completion (with an
  open-output shortcut), and failures (with the last stderr line).
- Graphical run progress panel instead of a raw terminal log: a stage stepper
  (待办 / 进行中 / 完成 / 跳过 / 失败 icons), a determinate progress bar with
  x/N and ETA for batch and multi-candidate runs (busy bar with a
  "this step takes a while" hint for single long API/inpaint stages), current
  item label, and elapsed time. The raw stdout/stderr is not shown; it is kept
  in memory and can be saved via 文件 → 导出运行日志 for troubleshooting.
- Integrated image preview with fit, 100%, zoom in, zoom out, wheel zoom,
  drag-and-drop, pixel/DPI/file-size info, and downsampled loading for very
  large print masters. The preview canvas stays neutral dark in both themes
  for stable print color judgement.
- Batch print asks for confirmation with the number of images before running
  and verifies the Real-ESRGAN binary is present.
- Window geometry, splitter positions, and per-page parameters persist across
  sessions via QSettings. Prompts and poster copy (per-run inputs) are not
  persisted.
- `QProcess` execution so the UI remains responsive while local workflows run.

## API Settings

The image API base URL and key are configured once in 文件 → 设置 and
persisted per-user via QSettings (`api/base_url`, `api/key`) — not in the repo,
and they survive app updates (a project file would be lost inside the packaged
`.app`). The key is stored in plain text in the OS user-settings store and is
injected into the workflow subprocess as `OPENAI_BASE_URL` / `OPENAI_API_KEY`;
if the fields are left blank the app falls back to those same environment
variables inherited from the shell. All API workflows (text-to-image,
full-poster, GPT rebuild) share this single configuration and refuse to run
until a key is available.

## Code Layout

- `desktop_qt_app.py`: thin entry point; dispatches `--worker` before any Qt
  import and re-exports GUI symbols lazily.
- `app_runtime.py`: Qt-free runtime helpers (paths, version, worker script
  dispatch). `DashDesignWorker` imports only this module.
- `ui/main_window.py`: main window, process lifecycle, preview panel.
- `ui/pages/`: one module per workflow page.
- `ui/widgets/`: shared widgets (PathField, ImagePreview with rectangle
  selection, InfoBanner, FlowLayout, ProgressPanel, SettingsDialog).
- `ui/api_config.py`: persisted app-wide API credentials (see API Settings).
- `ui/theme.py`: semantic design tokens and the light/dark QSS overlay on top
  of the qdarktheme base.
- `ui/commands.py`: pure, unit-tested command builders (`tests/`).
- `ui/progress.py` + `scripts/progress.py`: the workflow progress protocol.
  Scripts emit `##DASH_PROGRESS##`-prefixed JSON lines (gated on the
  `DASHDESIGN_PROGRESS=1` env the GUI sets, so plain CLI runs stay unchanged);
  the GUI parses them into the progress panel. Real-time delivery relies on
  `app_runtime` setting the worker's stdout to line-buffered before running
  the script.

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
