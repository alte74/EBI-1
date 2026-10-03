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


def vision_model() -> str:
    return os.getenv("VISION_MODEL", "").strip() or anthropic_model()


def vision_enabled() -> bool:
    return os.getenv("VISION_AGENT", "on").strip().lower() not in {"0", "off", "false", "no"}


def vision_camera_index() -> int:
    return int(os.getenv("VISION_CAMERA_INDEX", "0"))


def vision_interval_seconds() -> float:
    return max(2.0, float(os.getenv("VISION_INTERVAL_SECONDS", "10")))
