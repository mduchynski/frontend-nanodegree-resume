#!/usr/bin/env python3
"""Start Jarvis. Open the printed URL in Microsoft Edge.

Nothing outside the standard library is imported at module level. A missing
dependency is the single likeliest thing to go wrong on a fresh machine --
usually because the virtual environment is not active in this terminal -- and
a plain explanation beats an ImportError traceback.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# import name -> what to tell the user it is for
REQUIRED = {
    "anthropic": "the Claude API",
    "fastapi": "the web server",
    "uvicorn": "the web server",
    "dotenv": "reading .env",
    "httpx2": "HTTP requests",
    "selectolax": "reading web pages",
    "tzdata": "timezones on Windows",
}


def in_virtualenv() -> bool:
    return sys.prefix != sys.base_prefix


def missing_packages() -> list[str]:
    return [name for name in REQUIRED if importlib.util.find_spec(name) is None]


def explain_missing(missing: list[str]) -> None:
    """Say what is missing and, more usefully, why."""
    activate = r".venv\Scripts\activate" if sys.platform == "win32" else "source .venv/bin/activate"
    venv_exists = (ROOT / ".venv").exists()

    print("\nJarvis cannot start: some dependencies are not installed.\n")
    for name in missing:
        print(f"  - {name}  ({REQUIRED[name]})")

    print(f"\nPython in use: {sys.executable}")

    if venv_exists and not in_virtualenv():
        # Overwhelmingly the common case: packages went into .venv, but this
        # terminal is running the system Python.
        print(
            "\nThe virtual environment exists but is not active in this terminal,\n"
            "so Python cannot see what you installed into it. Run:\n"
            f"\n    {activate}\n    python run.py\n"
            "\nYou will know it worked because the prompt starts with (.venv).\n"
            "Every new terminal needs that activate line again -- or just use\n"
            "start.bat, which handles it for you."
        )
    elif not venv_exists:
        print(
            "\nThere is no .venv folder here, so setup has not been run yet:\n"
            "\n    python -m venv .venv\n"
            f"    {activate}\n"
            "    pip install -r requirements.txt\n"
            "\nOr just run start.bat, which does all of it."
        )
    else:
        print(
            "\nThe virtual environment is active but incomplete. Finish it with:\n"
            "\n    pip install -r requirements.txt\n"
        )
    print()


def preflight() -> list[str]:
    """Configuration problems that would otherwise surface mid-conversation."""
    from core.config import ROOT as CONFIG_ROOT, cfg, tz

    problems = []

    if not (CONFIG_ROOT / ".env").exists():
        problems.append(
            "No .env file. Run:  copy .env.example .env   then add your API key."
        )
    if not cfg.anthropic_api_key:
        problems.append(
            "ANTHROPIC_API_KEY is not set in .env. Get one at console.anthropic.com."
        )
    elif not cfg.anthropic_api_key.startswith("sk-ant-"):
        problems.append(
            "ANTHROPIC_API_KEY does not look like an Anthropic key (expected sk-ant-...)."
        )
    try:
        tz()
    except RuntimeError as exc:
        problems.append(str(exc))

    return problems


def main() -> None:
    if sys.version_info < (3, 11):
        print(
            f"\nJarvis needs Python 3.11 or newer; this is "
            f"{sys.version_info.major}.{sys.version_info.minor}.\n"
        )
        raise SystemExit(1)

    missing = missing_packages()
    if missing:
        explain_missing(missing)
        raise SystemExit(1)

    problems = preflight()
    if problems:
        print("\nJarvis cannot start yet:\n")
        for problem in problems:
            print(f"  - {problem}")
        print()
        raise SystemExit(1)

    import uvicorn

    from core.config import cfg

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
    )
    url = f"http://{cfg.host}:{cfg.port}"

    print("\n" + "=" * 58)
    print("  J A R V I S")
    print("=" * 58)
    print(f"  Interface : {url}")
    print(f"  Models    : {cfg.fast_model} / {cfg.deep_model}")
    print(f"  Email     : {'connected' if cfg.google_enabled else 'not set up'}")
    from tools import outlook_backend

    backend = outlook_backend()
    work = {
        "graph": "Microsoft 365 (Graph)",
        "local": "Outlook desktop app",
    }.get(backend, "not set up")
    print(f"  Work mail : {work}")
    print(f"  Calendar  : {work}")
    print(f"  Search    : {'Brave' if cfg.brave_api_key else 'DuckDuckGo (free fallback)'}")
    print(f"  Images    : {cfg.image_provider if cfg.images_enabled else 'not set up'}")
    print(f"  3D        : {'Tripo3D' if cfg.tripo_enabled else 'not set up'}")
    if backend == "off" and sys.platform == "win32":
        print("=" * 58)
        print("  Work mail/calendar is off. For the local Outlook route run:")
        print("      pip install -r requirements.txt")
    print("=" * 58)
    print("  Open in Microsoft Edge for the best voice.  Ctrl+C to stop.")
    print("=" * 58 + "\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    try:
        uvicorn.run("core.server:app", host=cfg.host, port=cfg.port, log_level="warning")
    except OSError as exc:
        if getattr(exc, "errno", None) in (48, 98, 10048):
            print(
                f"\nPort {cfg.port} is already in use -- most likely Jarvis is already\n"
                f"running in another window. Close it, or set JARVIS_PORT in .env.\n"
            )
            raise SystemExit(1)
        raise


if __name__ == "__main__":
    main()
