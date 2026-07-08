# DashDesign

DashDesign is a local desktop workflow tool for turning agent-generated poster
images into print-ready assets.

The current desktop client is built with PySide6 / Qt and wraps the validated
image workflows:

- project baseline preview for the current to-C poster generation draft
- baseline-aware text-to-image poster background generation
- batch print output with style-preserved super-resolution
- GPT Image rebuild package generation/execution
- QR-code area removal for manual QR replacement

## Start The Desktop Client

```bash
./run_desktop_tool.sh
```

Install dependencies on a fresh machine:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -r requirements-desktop.txt
./scripts/bootstrap_runtime_assets.sh
./run_desktop_tool.sh
```

## Test

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests/ -q
```

## Package

```bash
./scripts/package_pyinstaller_app.sh
```

## Release Engineering

See:

- `docs/desktop_tool.md`
- `docs/release_engineering.md`
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml`

Runtime model assets and generated print outputs are not committed. Use
`scripts/bootstrap_runtime_assets.sh` to fetch the required Real-ESRGAN runtime
files for the current platform.
