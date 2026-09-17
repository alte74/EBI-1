from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sentient_ai.personality import Personality


class Smell(str, Enum):
    NONE = "none"
    ROTTEN_EGGS = "rotten_eggs"
    FLOWERS = "flowers"


@dataclass
class Band:
    low: float
    high: float
    label: str

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def as_dict(self) -> dict:
        return {"low": self.low, "high": self.high, "label": self.label}


HUMIDITY_BANDS = (
    Band(0.0, 0.30, "dry"),
    Band(0.31, 0.70, "normal"),
    Band(0.71, 1.0, "wet"),
)

BAND_LABELS = {
    "touch": ("cold", "normal", "hot"),
    "vision": ("too_dark", "normal", "too_bright"),
    "hearing": ("low", "normal", "loud"),
    "humidity": ("dry", "normal", "wet"),
}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def bands_from_cuts(kind: str, low_cut: float, high_cut: float) -> tuple[Band, Band, Band]:
    labels = BAND_LABELS[kind]
    low_end = _clamp(low_cut)
    high_start = _clamp(high_cut)
    if high_start - low_end < 0.12:
        mid = (low_end + high_start) / 2
        low_end = _clamp(mid - 0.06)
        high_start = _clamp(mid + 0.06)
    return (
        Band(0.0, low_end, labels[0]),
        Band(round(low_end + 0.01, 4), high_start, labels[1]),
        Band(round(high_start + 0.01, 4), 1.0, labels[2]),
    )


@dataclass
class SensoryState:
    """Draft v1.0 sensors. Values are 0..1 unless noted."""

    volume: float = 0.50
    light: float = 0.55
    temperature: float = 0.50
    humidity: float = 0.45
    smell: Smell = Smell.NONE
    surface_touch: Optional[str] = None  # hard/soft/cold/hot, one-shot

    def __post_init__(self) -> None:
        self.volume = _clamp(self.volume)
        self.light = _clamp(self.light)
        self.temperature = _clamp(self.temperature)
        self.humidity = _clamp(self.humidity)

    def set_continuous(self, name: str, value: float) -> None:
        if name not in {"volume", "light", "temperature", "humidity"}:
            raise ValueError(f"Unknown continuous sensor: {name}")
        setattr(self, name, _clamp(value))

    def set_smell(self, smell: str) -> None:
        key = smell.strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "none": Smell.NONE,
            "off": Smell.NONE,
            "clear": Smell.NONE,
            "rotten": Smell.ROTTEN_EGGS,
            "rotten_eggs": Smell.ROTTEN_EGGS,
            "rotteneggs": Smell.ROTTEN_EGGS,
            "eggs": Smell.ROTTEN_EGGS,
            "flowers": Smell.FLOWERS,
            "flower": Smell.FLOWERS,
        }
        if key not in aliases:
            raise ValueError("Smell must be none, rotten_eggs, or flowers")
        self.smell = aliases[key]

    def set_surface(self, touch: str | None) -> None:
        if touch is None or str(touch).strip().lower() in {"", "none"}:
            self.surface_touch = None
            return
        key = str(touch).strip().lower()
        if key not in {"hard", "soft", "cold", "hot"}:
            raise ValueError("Surface touch must be hard, soft, cold, hot, or none")
        self.surface_touch = key

    def copy(self) -> SensoryState:
        return SensoryState(
            volume=self.volume,
            light=self.light,
            temperature=self.temperature,
            humidity=self.humidity,
            smell=self.smell,
            surface_touch=self.surface_touch,
        )

    def as_dict(self) -> dict:
        return {
            "volume": self.volume,
            "light": self.light,
            "lumens": self.light,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "smell": self.smell.value,
            "surface_touch": self.surface_touch or "none",
        }

    @classmethod
    def from_dict(cls, data: dict) -> SensoryState:
        state = cls()
        if "volume" in data:
            state.set_continuous("volume", float(data["volume"]))
        light = data.get("light", data.get("lumens"))
        if light is not None:
            state.set_continuous("light", float(light))
        if "temperature" in data:
            state.set_continuous("temperature", float(data["temperature"]))
        if "humidity" in data:
            state.set_continuous("humidity", float(data["humidity"]))
        if "smell" in data:
            state.set_smell(str(data["smell"]))
        if "surface_touch" in data:
            state.set_surface(data["surface_touch"])
        return state


@dataclass
class SensoryBands:
    hearing: tuple[Band, Band, Band]
    vision: tuple[Band, Band, Band]
    touch: tuple[Band, Band, Band]
    humidity: tuple[Band, Band, Band]

    @classmethod
    def for_personality(cls, personality: Personality) -> SensoryBands:
        cuts = personality.thresholds
        return cls(
            hearing=bands_from_cuts("hearing", cuts["hearing"]["low"], cuts["hearing"]["high"]),
            vision=bands_from_cuts("vision", cuts["vision"]["low"], cuts["vision"]["high"]),
            touch=bands_from_cuts("touch", cuts["touch"]["low"], cuts["touch"]["high"]),
            humidity=bands_from_cuts("humidity", cuts["humidity"]["low"], cuts["humidity"]["high"]),
        )


def classify(value: float, bands: tuple[Band, Band, Band]) -> str:
    for band in bands:
        if band.contains(value):
            return band.label
    if value < bands[0].high:
        return bands[0].label
    return bands[2].label


def snapshot_labels(state: SensoryState, bands: SensoryBands) -> dict[str, str]:
    return {
        "hearing": classify(state.volume, bands.hearing),
        "vision": classify(state.light, bands.vision),
        "touch": classify(state.temperature, bands.touch),
        "humidity": classify(state.humidity, bands.humidity),
        "smell": state.smell.value,
        "surface": state.surface_touch or "none",
    }


def all_normal(labels: dict[str, str]) -> bool:
    return (
        labels["hearing"] == "normal"
        and labels["vision"] == "normal"
        and labels["touch"] == "normal"
        and labels["humidity"] == "normal"
        and labels["smell"] == Smell.NONE.value
        and labels.get("surface", "none") == "none"
    )


def bands_payload(bands: SensoryBands) -> dict:
    return {
        "hearing": [b.as_dict() for b in bands.hearing],
        "vision": [b.as_dict() for b in bands.vision],
        "touch": [b.as_dict() for b in bands.touch],
        "humidity": [b.as_dict() for b in bands.humidity],
    }
