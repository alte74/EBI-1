from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from sentient_ai.config import ROOT

PRIMARY_EMOTIONS = (
    "Neutral",
    "Joy",
    "Sadness",
    "Fear",
    "Anger",
    "Surprise",
    "Disgust",
)

DRAFT_ALIASES = {
    "Happy": "Joy",
    "Angry": "Anger",
    "Scared": "Fear",
    "Disgusted": "Disgust",
}

DEFAULT_EMOTION_MAP = {
    "touch": {"cold": "Sadness", "normal": "Neutral", "hot": "Anger"},
    "vision": {"too_dark": "Fear", "normal": "Neutral", "too_bright": "Surprise"},
    "hearing": {"low": "Surprise", "normal": "Neutral", "loud": "Anger"},
    "humidity": {"dry": "Sadness", "normal": "Neutral", "wet": "Disgust"},
    "smell": {
        "none": "Neutral",
        "flowers": "Joy",
        "rotten_eggs": "Disgust",
    },
    "surface": {
        "none": "Neutral",
        "soft": "Joy",
        "hard": "Surprise",
        "cold": "Surprise",
        "hot": "Anger",
    },
}

BAND_INTENSITY = {
    ("touch", "hot"): 0.72,
    ("touch", "cold"): 0.55,
    ("touch", "normal"): 0.30,
    ("vision", "too_bright"): 0.60,
    ("vision", "too_dark"): 0.62,
    ("vision", "normal"): 0.30,
    ("hearing", "loud"): 0.68,
    ("hearing", "low"): 0.45,
    ("hearing", "normal"): 0.30,
    ("smell", "rotten_eggs"): 0.85,
    ("smell", "flowers"): 0.70,
    ("smell", "none"): 0.30,
    ("humidity", "wet"): 0.40,
    ("humidity", "dry"): 0.35,
    ("humidity", "normal"): 0.30,
    ("surface", "hot"): 0.70,
    ("surface", "cold"): 0.55,
    ("surface", "hard"): 0.48,
    ("surface", "soft"): 0.50,
    ("surface", "none"): 0.30,
}

EMOTION_MAP_PATH = ROOT / "emotion_map.json"
THRESHOLDS_PATH = ROOT / "thresholds.json"

DEFAULT_THRESHOLDS = {
    "touch": {"low": 0.30, "high": 0.70},
    "vision": {"low": 0.45, "high": 0.77},
    "hearing": {"low": 0.30, "high": 0.70},
    "humidity": {"low": 0.30, "high": 0.70},
}

MIN_THRESHOLD_GAP = 0.12


def normalize_emotion(name: str) -> str:
    key = str(name).strip()
    key = DRAFT_ALIASES.get(key, key)
    titled = key[:1].upper() + key[1:].lower() if key else "Neutral"
    titled = DRAFT_ALIASES.get(titled, titled)
    if titled not in PRIMARY_EMOTIONS:
        raise ValueError(f"Unknown emotion: {name}")
    return titled


def default_emotion_map() -> dict:
    return copy.deepcopy(DEFAULT_EMOTION_MAP)


def merge_emotion_map(incoming: dict | None) -> dict:
    merged = default_emotion_map()
    if not incoming:
        return merged
    for sense, bands in incoming.items():
        if sense not in merged or not isinstance(bands, dict):
            continue
        for band, emotion in bands.items():
            if band in merged[sense]:
                merged[sense][band] = normalize_emotion(emotion)
    return merged


def load_emotion_map() -> dict:
    if not EMOTION_MAP_PATH.exists():
        return default_emotion_map()
    try:
        data = json.loads(EMOTION_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_emotion_map()
    return merge_emotion_map(data)


def save_emotion_map(mapping: dict) -> None:
    EMOTION_MAP_PATH.write_text(json.dumps(mapping, indent=2), encoding="utf-8")


def default_thresholds() -> dict:
    return copy.deepcopy(DEFAULT_THRESHOLDS)


def normalize_cuts(low: float, high: float) -> tuple[float, float]:
    low_cut = max(0.05, min(0.85, float(low)))
    high_cut = max(0.15, min(0.95, float(high)))
    if high_cut - low_cut < MIN_THRESHOLD_GAP:
        mid = (low_cut + high_cut) / 2
        low_cut = max(0.05, mid - MIN_THRESHOLD_GAP / 2)
        high_cut = min(0.95, mid + MIN_THRESHOLD_GAP / 2)
    return round(low_cut, 3), round(high_cut, 3)


def merge_thresholds(incoming: dict | None) -> dict:
    merged = default_thresholds()
    if not incoming:
        return merged
    for sense, cuts in incoming.items():
        if sense not in merged or not isinstance(cuts, dict):
            continue
        low = cuts.get("low", merged[sense]["low"])
        high = cuts.get("high", merged[sense]["high"])
        low_cut, high_cut = normalize_cuts(low, high)
        merged[sense] = {"low": low_cut, "high": high_cut}
    return merged


def load_thresholds() -> dict:
    if not THRESHOLDS_PATH.exists():
        return default_thresholds()
    try:
        data = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_thresholds()
    return merge_thresholds(data)


def save_thresholds(mapping: dict) -> None:
    THRESHOLDS_PATH.write_text(json.dumps(mapping, indent=2), encoding="utf-8")


@dataclass
class EmotionalState:
    name: str = "Neutral"
    intensity: float = 0.35

    def __post_init__(self) -> None:
        self.name = DRAFT_ALIASES.get(self.name, self.name)
        if self.name not in PRIMARY_EMOTIONS:
            raise ValueError(f"Unknown emotion: {self.name}")
        self.intensity = max(0.0, min(1.0, float(self.intensity)))

    def set(self, name: str, intensity: float) -> None:
        self.name = DRAFT_ALIASES.get(name, name)
        if self.name not in PRIMARY_EMOTIONS:
            raise ValueError(f"Unknown emotion: {self.name}")
        self.intensity = max(0.0, min(1.0, float(intensity)))


def emotion_from_sense(
    sense: str,
    band: str,
    mapping: dict | None = None,
) -> tuple[str, float]:
    table = mapping or DEFAULT_EMOTION_MAP
    name = (table.get(sense) or {}).get(band, "Neutral")
    try:
        name = normalize_emotion(name)
    except ValueError:
        name = "Neutral"
    intensity = BAND_INTENSITY.get((sense, band), 0.45)
    return name, intensity


def tone_instructions(state: EmotionalState) -> str:
    tones = {
        "Neutral": "Speak in an even, easygoing conversational tone.",
        "Joy": "Sound warm, upbeat, a little playful. You may praise lightly.",
        "Sadness": "Sound quieter, slower, a bit wistful. Stay kind.",
        "Fear": "Sound unsettled, a little jumpy, checking that things are okay.",
        "Anger": "Sound irritated and clipped. Do not become abusive or threatening.",
        "Surprise": "Sound caught off guard, slightly breathless, curious.",
        "Disgust": "Sound recoiling, wrinkling your nose at whatever just hit you.",
    }
    return (
        f"Current Emotional State: {state.name} "
        f"(intensity {state.intensity:.2f}). {tones[state.name]}"
    )
