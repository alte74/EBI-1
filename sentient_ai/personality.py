from __future__ import annotations

from dataclasses import dataclass, field

from sentient_ai.emotion import load_emotion_map, load_thresholds


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class Personality:
    """Personality Programming — Individual_001, model type 1 (simple)."""

    individual_id: str = "Individual_001"
    model_type: str = "1 simple"
    ethical: float = 0.80
    patient: float = 0.45
    emotional: float = 0.62
    passionate: float = 0.55
    logical: float = 0.48
    sureness: float = 0.58
    dominant: float = 0.42
    laugh_frequency: float = 0.35
    praiser: float = 0.40
    excusing: float = 0.50
    forgiving: float = 0.55
    compassionate: float = 0.70
    emotion_map: dict = field(default_factory=load_emotion_map)
    thresholds: dict = field(default_factory=load_thresholds)

    def __post_init__(self) -> None:
        for name in (
            "ethical",
            "patient",
            "emotional",
            "passionate",
            "logical",
            "sureness",
            "dominant",
            "laugh_frequency",
            "praiser",
            "excusing",
            "forgiving",
            "compassionate",
        ):
            setattr(self, name, _clamp(getattr(self, name)))

    def threshold_shift(self) -> float:
        """Positive shift = more sensitive (narrower 'normal' band)."""
        return 0.14 * (self.emotional - self.patient)

    def interrupt_delay_scale(self) -> float:
        """Patient personalities wait longer before speaking up."""
        return 1.0 + 0.5 * self.patient - 0.35 * self.emotional

    def as_prompt(self) -> str:
        traits = [
            f"ethical={self.ethical:.2f}",
            f"patient={self.patient:.2f}",
            f"emotional={self.emotional:.2f}",
            f"passionate={self.passionate:.2f}",
            f"logical={self.logical:.2f}",
            f"sureness={self.sureness:.2f}",
            f"dominant={self.dominant:.2f}",
            f"laugh_frequency={self.laugh_frequency:.2f}",
            f"praiser={self.praiser:.2f}",
            f"excusing={self.excusing:.2f}",
            f"forgiving={self.forgiving:.2f}",
            f"compassionate={self.compassionate:.2f}",
        ]
        return (
            f"You are {self.individual_id} (model {self.model_type}). "
            "Let these personality sliders shape tone, not lecture topics: "
            + ", ".join(traits)
            + "."
        )

    def as_dict(self) -> dict:
        return {
            "individual_id": self.individual_id,
            "model_type": self.model_type,
            "ethical": self.ethical,
            "patient": self.patient,
            "emotional": self.emotional,
            "passionate": self.passionate,
            "logical": self.logical,
            "sureness": self.sureness,
            "dominant": self.dominant,
            "laugh_frequency": self.laugh_frequency,
            "praiser": self.praiser,
            "excusing": self.excusing,
            "forgiving": self.forgiving,
            "compassionate": self.compassionate,
            "emotion_map": self.emotion_map,
            "thresholds": self.thresholds,
        }
