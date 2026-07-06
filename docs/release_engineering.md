# DashDesign Release Engineering

This project now has a desktop release pipeline skeleton for macOS and Windows.

## Local Git

Initialize and commit locally:

```bash
git init -b main
git add .
git commit -m "Initialize DashDesign desktop release pipeline"
```

Bind a remote after creating the repository:

```bash
./scripts/setup_git_remote.sh git@github.com:ORG/REPO.git
git push -u origin main
```

## CI/CD

GitHub Actions workflows:

- `.github/workflows/ci.yml`
  - Runs on macOS and Windows.
  - Installs Python, runtime dependencies, and PySide6 Essentials.
  - Compiles Python files.
  - Starts the Qt client offscreen and validates worker command wiring.
- `.github/workflows/release.yml`
  - Runs on tags matching `v*` or manual workflow dispatch.
  - Builds macOS and Windows desktop artifacts.
  - Signs/notarizes when secrets are configured.
  - Builds a macOS DMG and Windows Inno Setup installer.
  - Uploads artifacts to GitHub Releases.
  - Generates `update-manifest.json` for the app update channel.

## Runtime Assets

Real-ESRGAN runtime assets are intentionally not committed. Bootstrap them with:

```bash
./scripts/bootstrap_runtime_assets.sh
```

The script downloads the official portable `realesrgan-ncnn-vulkan` package for
the current platform and copies the binary/models into `tools/`.

## Packaging

Local packaging entrypoint:

```bash
./scripts/package_qt_app.sh
```

The packaging config is `pysidedeploy.spec`. It builds standalone output and
includes:

- `desktop_qt_app.py`
- `scripts/`
- `tools/`
- `requirements-desktop.txt`

## macOS Signing And Notarization

Required GitHub secrets:

- `MACOS_CERTIFICATE_BASE64`
- `MACOS_CERTIFICATE_PASSWORD`
- `MACOS_CODESIGN_IDENTITY`
- `APPLE_ID`
- `APPLE_APP_SPECIFIC_PASSWORD`
- `APPLE_TEAM_ID`

Optional:

- `APPLE_NOTARY_KEYCHAIN_PROFILE`

Local signing/notarization:

```bash
export MACOS_CODESIGN_IDENTITY="Developer ID Application: ..."
export APPLE_ID="..."
export APPLE_APP_SPECIFIC_PASSWORD="..."
export APPLE_TEAM_ID="..."
./packaging/macos/sign_notarize.sh path/to/DashDesign.app
./packaging/macos/create_dmg.sh path/to/DashDesign.app 0.1.0
```

## Windows Signing And Installer

Required GitHub secrets for PFX-based signing:

- `WINDOWS_CERTIFICATE_BASE64`
- `WINDOWS_CERTIFICATE_PASSWORD`

Optional:

- `WINDOWS_TIMESTAMP_URL`

Local signing:

```powershell
.\packaging\windows\sign.ps1 -Path .\dist\DashDesign.exe
```

Build installer:

```powershell
.\packaging\windows\build_installer.ps1 -Version 0.1.0 -SourceDir .\dist\DashDesign -OutputDir .\dist
```

The installer definition is `packaging/windows/DashDesign.iss`.

## Auto Update Channel

The Qt client supports a manifest-based update check. Configure:

```bash
export DASHDESIGN_UPDATE_MANIFEST_URL="https://github.com/ORG/REPO/releases/latest/download/update-manifest.json"
```

The release workflow writes the same URL into `UPDATE_MANIFEST_URL` before
packaging, so release builds can check for updates without requiring an
environment variable. The environment variable remains an override for staging
or private update channels.

The app checks the manifest URL on startup when configured, compares the
manifest version with `APP_VERSION`, and opens the platform installer download
URL when a newer version exists.

Generated manifest example:

```json
{
  "version": "0.1.0",
  "notes": "DashDesign 0.1.0",
  "platforms": {
    "macos": {
      "url": "https://github.com/ORG/REPO/releases/download/v0.1.0/DashDesign-0.1.0-macos.dmg",
      "sha256": "..."
    },
    "windows": {
      "url": "https://github.com/ORG/REPO/releases/download/v0.1.0/DashDesign-0.1.0-windows-setup.exe",
      "sha256": "..."
    }
  }
}
```

This is a production-safe first step. Fully silent in-app self-replacement should
be evaluated separately with Sparkle on macOS and WinSparkle or an equivalent
signed updater on Windows after the signing identities and release host are
finalized.
