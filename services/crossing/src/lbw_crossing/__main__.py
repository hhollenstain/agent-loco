"""Run the engineer dashboard. The organization site is a different process."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from lbw_crossing.app import create_app


def main() -> None:
    root = Path(os.environ.get("LBW_STORE_ROOT", "/workspaces"))
    url = os.environ.get("LBW_DATABASE_URL", "")
    host = os.environ.get("LBW_HOST", "0.0.0.0")
    port = int(os.environ.get("LBW_PORT", "8080"))
    uvicorn.run(create_app(root, url), host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
