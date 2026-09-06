#!/usr/bin/env python3
"""Start Jarvis. Open the printed URL in Chrome or Edge."""
from __future__ import annotations

import logging
import webbrowser

import uvicorn

from core.config import ROOT, cfg, tz


def preflight() -> list[str]:
    """Catch the things that would otherwise fail mid-conversation."""
    problems = []

    if not (ROOT / ".env").exists():
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
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s"
    )
    problems = preflight()
    if problems:
        print("\nJarvis cannot start yet:\n")
        for problem in problems:
            print(f"  - {problem}")
        print()
        raise SystemExit(1)

    url = f"http://{cfg.host}:{cfg.port}"

    print("\n" + "=" * 58)
    print("  J A R V I S")
    print("=" * 58)
    print(f"  Interface : {url}")
    print(f"  Models    : {cfg.fast_model} / {cfg.deep_model}")
    print(f"  Email     : {'connected' if cfg.google_enabled else 'not set up'}")
    print(f"  Calendar  : {'connected' if cfg.microsoft_enabled else 'not set up'}")
    print(f"  Search    : {'Brave' if cfg.brave_api_key else 'DuckDuckGo (free fallback)'}")
    print(f"  Images    : {cfg.image_provider if cfg.images_enabled else 'not set up'}")
    print(f"  3D        : {'Tripo3D' if cfg.tripo_enabled else 'not set up'}")
    print("=" * 58)
    print("  Open in Microsoft Edge for the best voice.")
    print("=" * 58 + "\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    uvicorn.run("core.server:app", host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
