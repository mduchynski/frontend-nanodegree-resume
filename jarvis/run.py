#!/usr/bin/env python3
"""Start Jarvis. Open the printed URL in Chrome or Edge."""
from __future__ import annotations

import logging
import webbrowser

import uvicorn

from core.config import cfg


def main() -> None:
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
    print(f"  Calendar  : {'connected' if cfg.microsoft_enabled else 'not set up'}")
    print(f"  Search    : {'Brave' if cfg.brave_api_key else 'DuckDuckGo (free fallback)'}")
    print("=" * 58 + "\n")

    try:
        webbrowser.open(url)
    except Exception:
        pass

    uvicorn.run("core.server:app", host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
