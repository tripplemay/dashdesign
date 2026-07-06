#!/usr/bin/env python3
"""Generate a DashDesign update manifest for release assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Release version, for example 0.1.0 or v0.1.0.")
    parser.add_argument("--notes", default="", help="Short release note shown in the updater prompt.")
    parser.add_argument("--macos-url", help="Download URL for the macOS artifact.")
    parser.add_argument("--windows-url", help="Download URL for the Windows artifact.")
    parser.add_argument("--macos-file", type=Path, help="Optional macOS artifact file for sha256.")
    parser.add_argument("--windows-file", type=Path, help="Optional Windows artifact file for sha256.")
    parser.add_argument("--output", type=Path, default=Path("dist/update-manifest.json"))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    platforms: dict[str, dict[str, str]] = {}

    if args.macos_url:
        platforms["macos"] = {"url": args.macos_url}
        if args.macos_file and args.macos_file.exists():
            platforms["macos"]["sha256"] = sha256_file(args.macos_file)

    if args.windows_url:
        platforms["windows"] = {"url": args.windows_url}
        if args.windows_file and args.windows_file.exists():
            platforms["windows"]["sha256"] = sha256_file(args.windows_file)

    manifest = {
        "version": args.version.lstrip("v"),
        "notes": args.notes,
        "platforms": platforms,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
