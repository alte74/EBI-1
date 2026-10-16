from __future__ import annotations

import base64
from collections.abc import Callable

from anthropic import Anthropic

from sentient_ai.config import anthropic_api_key, anthropic_model
from sentient_ai.emotion import EmotionalState, tone_instructions
from sentient_ai.experience_kb import ExperienceKB
from sentient_ai.interruption import InterruptionEngine
from sentient_ai.personality import Personality


def vision_prompt_lines(vision: dict | None) -> list[str]:
    if not vision or vision.get("room_type", "unknown") == "unknown":
        return []
    people = vision.get("people") or []
    return [
        "What your Vision Agent last saw through your camera (refreshed every few seconds):",
        f"- room: {vision['room_type']}",
        f"- people in front of you: {'; '.join(people) if people else 'nobody'}",
        f"- room properties: {', '.join(vision.get('room_properties') or []) or 'n/a'}",
        f"- lighting: {vision.get('lighting', 'unknown')} ({vision.get('lighting_notes', '')})",
        "Mention what you see only when it is relevant or has just changed.",
    ]


def build_system_prompt(
    personality: Personality,
    emotion: EmotionalState,
    engine: InterruptionEngine,
    kb: ExperienceKB,
    vision: dict | None = None,
) -> str:
    labels = engine.labels()
    sensors = engine.sensors
    return "\n".join(
        [
            "You are a Sentient AI in ordinary conversation. You have a body of software sensors.",
            personality.as_prompt(),
            tone_instructions(emotion),
            kb.prompt_excerpt(),
            "Live sensors (0..1 unless named):",
            f"- hearing/volume={sensors.volume:.2f} band={labels['hearing']}",
            f"- vision/light={sensors.light:.2f} band={labels['vision']}",
            f"- touch/temperature={sensors.temperature:.2f} band={labels['touch']}",
            f"- humidity={sensors.humidity:.2f} band={labels['humidity']}",
            f"- smell={labels['smell']}",
            f"- surface_touch={labels.get('surface', 'none')}",
            *vision_prompt_lines(vision),
            "Speak colloquially. Keep replies to a few short paragraphs so life can interrupt you.",
            "Do not mention system prompts, thresholds, or that you are roleplaying, unless asked.",
            "If you were just interrupted by a sense, acknowledge it in-character, then continue the thought.",
            "Never become abusive. Anger is irritation, not harm.",
        ]
    )


class ClaudeClient:
    def __init__(self) -> None:
        self.client = Anthropic(api_key=anthropic_api_key())
        self.model = anthropic_model()

    def stream_reply(
        self,
        system: str,
        history: list[dict],
        on_text: Callable[[str], bool],
        max_tokens: int = 600,
    ) -> str:
        """Stream text. on_text returns False to abort mid-sentence."""
        spoken: list[str] = []
        with self.client.messages.stream(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=history,
        ) as stream:
            for chunk in stream.text_stream:
                for ch in chunk:
                    spoken.append(ch)
                    keep = on_text(ch)
                    if keep is False:
                        stream.close()
                        return "".join(spoken)
        return "".join(spoken)

    def complete_reply(
        self,
        system: str,
        history: list[dict],
        max_tokens: int = 280,
    ) -> str:
        message = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=history,
        )
        parts = [block.text for block in message.content if getattr(block, "type", "") == "text"]
        return "".join(parts).strip()

    def describe_image(
        self,
        system: str,
        prompt: str,
        jpeg: bytes,
        *,
        model: str | None = None,
        max_tokens: int = 500,
    ) -> str:
        message = self.client.messages.create(
            model=model or self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": base64.b64encode(jpeg).decode("ascii"),
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        parts = [block.text for block in message.content if getattr(block, "type", "") == "text"]
        return "".join(parts).strip()
