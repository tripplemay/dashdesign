#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

mkdir -p tools tools/models .cache/downloads

case "${RUNNER_OS:-$(uname -s)}" in
  macOS|Darwin)
    archive="realesrgan-ncnn-vulkan-20220424-macos.zip"
    url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/${archive}"
    binary_name="realesrgan-ncnn-vulkan"
    ;;
  Windows|MINGW*|MSYS*|CYGWIN*)
    archive="realesrgan-ncnn-vulkan-20220424-windows.zip"
    url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/${archive}"
    binary_name="realesrgan-ncnn-vulkan.exe"
    ;;
  *)
    archive="realesrgan-ncnn-vulkan-20220424-ubuntu.zip"
    url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/${archive}"
    binary_name="realesrgan-ncnn-vulkan"
    ;;
esac

archive_path=".cache/downloads/${archive}"
extract_dir=".cache/realesrgan"

if [[ ! -f "$archive_path" ]]; then
  echo "Downloading ${archive}..."
  curl -L "$url" -o "$archive_path"
fi

rm -rf "$extract_dir"
mkdir -p "$extract_dir"
unzip -q "$archive_path" -d "$extract_dir"

found_binary="$(find "$extract_dir" -type f -name "$binary_name" | head -n 1)"
if [[ -z "$found_binary" ]]; then
  echo "Could not find ${binary_name} in ${archive}" >&2
  exit 1
fi

cp "$found_binary" "tools/${binary_name}"
chmod +x "tools/${binary_name}" 2>/dev/null || true

found_models="$(find "$extract_dir" -type d -name models | head -n 1)"
if [[ -z "$found_models" ]]; then
  echo "Could not find models directory in ${archive}" >&2
  exit 1
fi

cp "$found_models"/*.param tools/models/
cp "$found_models"/*.bin tools/models/

echo "Runtime assets ready:"
echo "- tools/${binary_name}"
echo "- tools/models"
