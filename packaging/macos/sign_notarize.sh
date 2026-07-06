#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:-}"
if [[ -z "$APP_PATH" || ! -d "$APP_PATH" ]]; then
  echo "Usage: $0 path/to/DashDesign.app" >&2
  exit 2
fi

: "${MACOS_CODESIGN_IDENTITY:?Set MACOS_CODESIGN_IDENTITY, for example 'Developer ID Application: ...'}"

APP_PATH="$(cd "$(dirname "$APP_PATH")" && pwd)/$(basename "$APP_PATH")"
ZIP_PATH="${APP_PATH%.app}-notary.zip"

codesign --force --deep --options runtime --timestamp --sign "$MACOS_CODESIGN_IDENTITY" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"

if [[ -n "${APPLE_NOTARY_KEYCHAIN_PROFILE:-}" ]]; then
  xcrun notarytool submit "$ZIP_PATH" --keychain-profile "$APPLE_NOTARY_KEYCHAIN_PROFILE" --wait
elif [[ -n "${APPLE_ID:-}" && -n "${APPLE_APP_SPECIFIC_PASSWORD:-}" && -n "${APPLE_TEAM_ID:-}" ]]; then
  xcrun notarytool submit "$ZIP_PATH" \
    --apple-id "$APPLE_ID" \
    --password "$APPLE_APP_SPECIFIC_PASSWORD" \
    --team-id "$APPLE_TEAM_ID" \
    --wait
else
  echo "Notarization credentials are not configured; app was signed but not notarized." >&2
  echo "Set APPLE_NOTARY_KEYCHAIN_PROFILE or APPLE_ID/APPLE_APP_SPECIFIC_PASSWORD/APPLE_TEAM_ID." >&2
  exit 3
fi

xcrun stapler staple "$APP_PATH"
spctl -a -vvv -t exec "$APP_PATH"

echo "$APP_PATH"
