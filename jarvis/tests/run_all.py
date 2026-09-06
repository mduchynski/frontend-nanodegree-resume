#!/usr/bin/env python3
"""Run every check. No API key and no network required -- these exercise the
routing, persistence, tool-gating and WebSocket logic with stubs."""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SUITES = ["test_core.py", "test_agent.py", "test_ws.py", "test_3d.py"]
JS_SUITES = ["test_wake.js"]


def main() -> int:
    failed = []
    for suite in SUITES:
        print(f"\n{'=' * 60}\n  {suite}\n{'=' * 60}")
        if subprocess.run([sys.executable, str(HERE / suite)]).returncode:
            failed.append(suite)

    node = shutil.which("node")
    for suite in JS_SUITES:
        print(f"\n{'=' * 60}\n  {suite}\n{'=' * 60}")
        if node is None:
            # Node is not needed to run Jarvis, only to check the browser code.
            print("  skipped -- node is not installed")
            continue
        if subprocess.run([node, str(HERE / suite)]).returncode:
            failed.append(suite)
    print(f"\n{'=' * 60}")
    print("ALL SUITES PASSED" if not failed else f"FAILED: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
