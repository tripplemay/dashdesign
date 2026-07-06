#!/usr/bin/env python3
"""Console worker entry point for packaged DashDesign workflows."""

from __future__ import annotations

import sys

from desktop_qt_app import run_script_worker


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "--worker":
        args = args[1:]
    if not args:
        print("Usage: DashDesignWorker --worker <workflow> [args...]", file=sys.stderr)
        return 2
    return run_script_worker(args[0], args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
