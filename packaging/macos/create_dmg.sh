#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:-}"
VERSION="${2:-0.1.0}"
if [[ -z "$APP_PATH" || ! -d "$APP_PATH" ]]; then
  echo "Usage: $0 path/to/DashDesign.app version" >&2
  exit 2
fi

DIST_DIR="dist"
DMG_PATH="${DIST_DIR}/DashDesign-${VERSION}-macos.dmg"
VOLUME_NAME="DashDesign ${VERSION}"

mkdir -p "$DIST_DIR"
rm -f "$DMG_PATH"

hdiutil create \
  -volname "$VOLUME_NAME" \
  -srcfolder "$APP_PATH" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

if [[ -n "${MACOS_CODESIGN_IDENTITY:-}" ]]; then
  codesign --force --timestamp --sign "$MACOS_CODESIGN_IDENTITY" "$DMG_PATH"
fi

echo "$DMG_PATH"
