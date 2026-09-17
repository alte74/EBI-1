from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def anthropic_api_key() -> str:
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key or key.startswith("sk-ant-your-key"):
        raise RuntimeError(
            "Missing ANTHROPIC_API_KEY. Put it in the project-root .env file:\n"
            "  ANTHROPIC_API_KEY=sk-ant-...\n"
            "See .env.example."
        )
    return key


def anthropic_model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5").strip()
