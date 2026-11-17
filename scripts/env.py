"""Load settings from the repository's .env file (not committed, see .env.example)."""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env() -> None:
    # variables already set in the environment take precedence
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip("\"'"))


def root() -> Path:
    load_env()
    if not os.environ.get("DYNOPT_ROOT"):
        raise SystemExit(f"DYNOPT_ROOT is not set; copy .env.example to {ENV_FILE} and edit it")
    return Path(os.environ["DYNOPT_ROOT"])
