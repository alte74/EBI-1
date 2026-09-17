from __future__ import annotations

from dataclasses import dataclass, field

from sentient_ai.emotion import EmotionalState, emotion_from_sense
from sentient_ai.personality import Personality
from sentient_ai.sensors import SensoryBands, SensoryState, all_normal, bands_payload, snapshot_labels
from sentient_ai.utterances import compose_interrupt

FLOW = r"""
Emotion by Interruption -- sequence

  User --speaks--> Chat loop
                      |
                      v
              Build prompt with:
                personality + current emotion + live sensors
                      |
                      v
              Claude starts streaming a reply
                      |
          +-----------+-----------+
          |                       |
          v                       v
   print tokens            random 5-10s monitor
   (mid-sentence)          (personality-scaled)
          |                       |
          |                       v
          |              compare sensors to last bands
          |              (edge trigger, not steady state)
          |                       |
          |         threshold crossed? --no--> keep talking
          |                       | yes
          |                       v
          |              STOP stream in the middle of a sentence
          |                       |
          |                       v
          |              Type 1 utterance  (Huh / Oh / Woah...)
          |              optional Type 2   (shout / yawn / laugh)
          |              colloquial comment about THAT sense
          |                       |
          |                       v
          |              map sense -> primary emotion
          |              update Current Emotional State
          |                       |
          +---------> resume remaining thought in the new tone
                      |
                      v
              HUD: emotion + sensor bands
              wait for next user line  (or /set /demo)
"""


@dataclass
class InterruptEvent:
    sense: str
    band: str
    value: str
    emotion: str
    intensity: float
    spoken: str


@dataclass
class InterruptionEngine:
    personality: Personality
    sensors: SensoryState
    emotion: EmotionalState
    last_labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.last_labels:
            self.last_labels = snapshot_labels(
                self.sensors, SensoryBands.for_personality(self.personality)
            )

    def bands(self) -> SensoryBands:
        return SensoryBands.for_personality(self.personality)

    def labels(self, sensors: SensoryState | None = None) -> dict[str, str]:
        return snapshot_labels(sensors or self.sensors, self.bands())

    def peek(self, sensors: SensoryState | None = None, *, stable: bool = False) -> InterruptEvent | None:
        """Like poll, but does not change last_labels or emotion."""
        state = sensors if sensors is not None else self.sensors
        current = snapshot_labels(state, self.bands())
        previous = self.last_labels
        for sense in ("touch", "vision", "hearing", "smell", "humidity", "surface"):
            if current.get(sense) == previous.get(sense, current.get(sense)):
                continue
            new_band = current[sense]
            emotion_name, intensity = emotion_from_sense(
                sense, new_band, self.personality.emotion_map
            )
            intensity = min(1.0, intensity * (0.7 + 0.6 * self.personality.emotional))
            spoken = compose_interrupt(
                sense, new_band, intensity, self.personality, stable=stable
            )
            return InterruptEvent(
                sense=sense,
                band=new_band,
                value=str(getattr(state, _sensor_attr(sense), new_band)),
                emotion=emotion_name,
                intensity=intensity,
                spoken=spoken,
            )
        return None

    def poll(self) -> InterruptEvent | None:
        """Fire only when a band *changes* — Emotion by Interruption, not steady state."""
        event = self.peek(stable=False)
        current = self.labels()
        self.last_labels = current
        if event:
            self.emotion.set(event.emotion, event.intensity)
        if self.sensors.surface_touch:
            self.sensors.surface_touch = None
            self.last_labels["surface"] = "none"
        return event

    def resulting_emotion(self, sensors: SensoryState | None = None) -> tuple[str, float]:
        state = sensors if sensors is not None else self.sensors
        event = self.peek(state, stable=True)
        if event:
            return event.emotion, event.intensity
        return self.emotion.name, self.emotion.intensity

    def apply_event(self, event: InterruptEvent) -> None:
        self.emotion.set(event.emotion, event.intensity)

    def hud(self) -> str:
        labels = self.labels()
        s = self.sensors
        return (
            f"[emotion: {self.emotion.name} {self.emotion.intensity:.2f} | "
            f"temp: {s.temperature:.2f}({labels['touch']}) | "
            f"light: {s.light:.2f}({labels['vision']}) | "
            f"vol: {s.volume:.2f}({labels['hearing']}) | "
            f"humidity: {s.humidity:.2f}({labels['humidity']}) | "
            f"smell: {labels['smell']}]"
        )

    def snapshot(self) -> dict:
        labels = self.labels()
        return {
            "emotion": {
                "name": self.emotion.name,
                "intensity": round(self.emotion.intensity, 3),
            },
            "sensors": self.sensors.as_dict(),
            "labels": labels,
            "last_labels": dict(self.last_labels),
            "bands": bands_payload(self.bands()),
            "all_normal": all_normal(labels),
        }


def _sensor_attr(sense: str) -> str:
    return {
        "touch": "temperature",
        "vision": "light",
        "hearing": "volume",
        "humidity": "humidity",
        "smell": "smell",
        "surface": "surface_touch",
    }[sense]


def event_payload(event: InterruptEvent | None) -> dict | None:
    if event is None:
        return None
    return {
        "sense": event.sense,
        "band": event.band,
        "value": event.value,
        "emotion": event.emotion,
        "intensity": round(event.intensity, 3),
        "spoken": event.spoken,
    }


def monitor_interval_seconds(personality: Personality) -> float:
    import random

    scale = personality.interrupt_delay_scale()
    return random.uniform(5.0, 10.0) * max(0.55, scale)
