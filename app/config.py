"""Loads .env before anything reads os.environ.

Kept dependency-free on purpose: if python-dotenv is not installed we still
work, you just have to export the variables in your shell instead.
"""

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = None) -> bool:
    """Load KEY=VALUE pairs from .env into os.environ. Existing vars win."""
    target = Path(path) if path else ENV_PATH
    if not target.is_file():
        return False

    try:
        from dotenv import load_dotenv

        load_dotenv(target, override=False)
        return True 
    except ImportError: 
        pass 

    # Minimal parser: KEY=VALUE, # comments, optio nal quotes.
    try:
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
        return True
    except OSError:
        return False


load_env()
