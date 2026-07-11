#!/usr/bin/env bash
# Regenerate all app-icon assets from assets/app_icon.svg.
#
# Run on macOS after editing the SVG. Requires: rsvg-convert (brew install
# librsvg), iconutil (macOS built-in), and Python with Pillow (the project's
# .venv). Outputs, all committed to git so CI just references them:
#   assets/app_icon.png    512px, for the in-app window icon (Qt setWindowIcon)
#   assets/app_icon.ico    multi-resolution, for the Windows exe + installer
#   assets/app_icon.icns   for the macOS .app bundle
set -euo pipefail

cd "$(dirname "$0")/.."
SVG="assets/app_icon.svg"
PY="${PYTHON:-.venv/bin/python}"

command -v rsvg-convert >/dev/null || { echo "need rsvg-convert (brew install librsvg)"; exit 1; }
command -v iconutil >/dev/null || { echo "need iconutil (macOS only)"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "== rasterize PNGs =="
for s in 16 32 48 64 128 256 512 1024; do
  rsvg-convert -w "$s" -h "$s" "$SVG" -o "$TMP/icon_${s}.png"
done
cp "$TMP/icon_512.png" assets/app_icon.png

echo "== app_icon.ico (Windows, multi-resolution) =="
"$PY" - "$TMP" <<'PY'
import sys
from PIL import Image
tmp = sys.argv[1]
sizes = [16, 32, 48, 64, 128, 256]
imgs = [Image.open(f"{tmp}/icon_{s}.png").convert("RGBA") for s in sizes]
# Base = largest; append the rest at their native (SVG-rendered) sizes so every
# resolution is crisp rather than downscaled from one source.
imgs[-1].save("assets/app_icon.ico", format="ICO", append_images=imgs[:-1])
print("wrote assets/app_icon.ico")
PY

echo "== app_icon.icns (macOS) =="
ICONSET="$TMP/app.iconset"
mkdir -p "$ICONSET"
cp "$TMP/icon_16.png"   "$ICONSET/icon_16x16.png"
cp "$TMP/icon_32.png"   "$ICONSET/icon_16x16@2x.png"
cp "$TMP/icon_32.png"   "$ICONSET/icon_32x32.png"
cp "$TMP/icon_64.png"   "$ICONSET/icon_32x32@2x.png"
cp "$TMP/icon_128.png"  "$ICONSET/icon_128x128.png"
cp "$TMP/icon_256.png"  "$ICONSET/icon_128x128@2x.png"
cp "$TMP/icon_256.png"  "$ICONSET/icon_256x256.png"
cp "$TMP/icon_512.png"  "$ICONSET/icon_256x256@2x.png"
cp "$TMP/icon_512.png"  "$ICONSET/icon_512x512.png"
cp "$TMP/icon_1024.png" "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o assets/app_icon.icns
echo "wrote assets/app_icon.icns"

echo "== done =="
ls -la assets/app_icon.*
