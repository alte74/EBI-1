from __future__ import annotations

import random
import re

from sentient_ai.emotion import EmotionalState
from sentient_ai.interruption import InterruptionEngine, event_payload
from sentient_ai.personality import Personality
from sentient_ai.sensors import SensoryState

CANONICAL_CONVERSATION = [
    {
        "role": "user",
        "text": "Hey — how are you today?",
    },
    {
        "role": "bot",
        "text": (
            "I'm ok, I think being creative is important because it motivates the mind "
            "to improve; curiosity and imagination are so cool and important to me!"
        ),
    },
    {
        "role": "user",
        "text": "That sounds like a good headspace. What have you been making?",
    },
    {
        "role": "bot",
        "text": (
            "Nothing huge, just small ideas I can turn over while the room stays this "
            "comfortable. When nothing is poking at me I can stay with a thought and "
            "actually enjoy it."
        ),
    },
    {
        "role": "user",
        "text": "Do you like that kind of quiet making?",
    },
    {
        "role": "bot",
        "text": (
            "Yeah. Making something, even a little thing, is how I remember I'm more "
            "than a reaction. It's calm, and it feels like the best version of me."
        ),
    },
]

SENSOR_ALIASES = {
    "temp": "temperature",
    "temperature": "temperature",
    "touch": "temperature",
    "light": "light",
    "lumens": "light",
    "vision": "light",
    "vol": "volume",
    "volume": "volume",
    "hearing": "volume",
    "humidity": "humidity",
    "humid": "humidity",
    "smell": "smell",
    "odor": "smell",
    "surface": "surface_touch",
    "surface_touch": "surface_touch",
}

DISPLAY_SENSOR = {
    "temperature": "temp",
    "light": "light",
    "volume": "volume",
    "humidity": "humidity",
    "smell": "smell",
    "surface_touch": "surface",
}

NAMED_CONTINUOUS = {
    "temperature": {"cold": 0.08, "hot": 0.92, "normal": 0.50},
    "light": {
        "dark": 0.10,
        "too_dark": 0.10,
        "bright": 0.90,
        "too_bright": 0.90,
        "normal": 0.55,
    },
    "volume": {
        "quiet": 0.08,
        "low": 0.08,
        "loud": 0.92,
        "normal": 0.50,
    },
    "humidity": {"dry": 0.08, "wet": 0.92, "normal": 0.45},
}

TAG_RE = re.compile(r"\[(\s*[A-Za-z_]+\s*=\s*[^\]]+?)\]")


def tokenize(text: str) -> list[str]:
    return (text or "").split()


def join_words(words: list[str]) -> str:
    return " ".join(words).strip()


def canonical_payload() -> dict:
    return {
        "environment": {
            "temperature": 0.50,
            "light": 0.55,
            "volume": 0.50,
            "humidity": 0.45,
            "smell": "none",
            "surface_touch": "none",
            "emotion": "Neutral",
        },
        "messages": [dict(item) for item in CANONICAL_CONVERSATION],
    }


def format_tag(sensor: str, value: str) -> str:
    key = SENSOR_ALIASES.get(str(sensor).strip().lower(), str(sensor).strip().lower())
    shown = DISPLAY_SENSOR.get(key, sensor)
    return f"[{shown} = {str(value).strip()}]"


def split_at_gap(words: list[str], gap: int) -> tuple[list[str], list[str]]:
    """gap is the 0-based space between words: after words[gap], before words[gap+1]."""
    if len(words) < 2:
        raise ValueError("Need at least two words to insert a value between them.")
    if gap < 0 or gap >= len(words) - 1:
        raise ValueError(f"Gap {gap} is out of range for {len(words)} words.")
    return words[: gap + 1], words[gap + 1 :]


def pick_remainder_cut(remainder: list[str], rng: random.Random | None = None) -> int:
    """How many remainder words to keep before the interrupt.

    Remainder space #1 is between remainder[0] and remainder[1]. A keep count of 5
    cuts between 'to' and 'improve' in the sample sentence.
    Always eliminates at least one word when the remainder has any.
    """
    rng = rng or random.Random()
    if len(remainder) <= 1:
        return 0
    return rng.randint(0, len(remainder) - 1)


def parse_numeric_value(raw: str) -> float:
    text = str(raw).strip()
    if re.fullmatch(r"\d{1,3}", text):
        return min(1.0, int(text) / 100.0)
    return max(0.0, min(1.0, float(text)))


def resolve_sensor_value(sensor: str, raw: str) -> tuple[str, float | str]:
    key = SENSOR_ALIASES.get(str(sensor).strip().lower())
    if not key:
        raise ValueError(f"Unknown sensor: {sensor}")
    token = str(raw).strip()
    named = token.lower().replace(" ", "_").replace("-", "_")
    if key == "smell":
        aliases = {
            "none": "none",
            "off": "none",
            "flowers": "flowers",
            "flower": "flowers",
            "rotten": "rotten_eggs",
            "rotten_eggs": "rotten_eggs",
        }
        if named not in aliases:
            raise ValueError("Smell must be none, flowers, or rotten_eggs.")
        return key, aliases[named]
    if key == "surface_touch":
        aliases = {"none": "none", "soft": "soft", "hard": "hard", "cold": "cold", "hot": "hot"}
        if named not in aliases:
            raise ValueError("Surface must be none, soft, hard, cold, or hot.")
        return key, aliases[named]
    table = NAMED_CONTINUOUS.get(key, {})
    if named in table:
        return key, table[named]
    try:
        return key, parse_numeric_value(token)
    except ValueError as exc:
        raise ValueError(f"Could not parse {key} value {raw!r}.") from exc


def apply_sensor_value(state: SensoryState, sensor: str, raw: str) -> SensoryState:
    next_state = state.copy()
    key, value = resolve_sensor_value(sensor, raw)
    if key == "smell":
        next_state.set_smell(str(value))
    elif key == "surface_touch":
        next_state.set_surface(None if value == "none" else str(value))
    else:
        next_state.set_continuous(key, float(value))
    return next_state


def parse_inline_tag(text: str) -> tuple[str, dict | None]:
    match = TAG_RE.search(text or "")
    if not match:
        return text, None
    inner = match.group(1)
    if "=" not in inner:
        return text, None
    sensor, value = inner.split("=", 1)
    before = (text or "")[: match.start()].strip()
    after = (text or "")[match.end() :].strip()
    left = tokenize(before)
    if not left:
        return text, None
    tag = {"gap": len(left) - 1, "sensor": sensor.strip(), "value": value.strip()}
    rebuilt = join_words(left + tokenize(after))
    return rebuilt, tag


def rewrite_remainder(
    client,
    personality: Personality,
    emotion_name: str,
    intensity: float,
    prefix: str,
    spoken: str,
    eliminated: str,
    engine: InterruptionEngine,
) -> str:
    if not eliminated or client is None:
        return ""
    from sentient_ai.claude_client import build_system_prompt
    from sentient_ai.experience_kb import ExperienceKB

    history = [
        {
            "role": "assistant",
            "content": prefix.rstrip() + "--",
        },
        {
            "role": "user",
            "content": (
                f"[sensory interruption. You already said out loud: {spoken} "
                f"Your emotion is now {emotion_name}. "
                f"You were originally going to finish with these exact words: {eliminated!r}. "
                "Rewrite that leftover fragment in the new tone and splice it as the next words. "
                "Keep it to one short sentence. Do not repeat the interrupt line or the prefix. "
                "No stage directions, no asterisks, no narrating gestures.]"
            ),
        },
    ]
    text = client.complete_reply(
        build_system_prompt(personality, engine.emotion, engine, ExperienceKB()),
        history,
        max_tokens=80,
    ).strip()
    return " ".join(text.replace("*", "").split())


def simulate_conversation(
    messages: list[dict],
    *,
    personality: Personality,
    client=None,
    rng: random.Random | None = None,
) -> dict:
    rng = rng or random.Random()
    sensors = SensoryState()
    engine = InterruptionEngine(
        personality=personality,
        sensors=sensors,
        emotion=EmotionalState("Neutral", 0.30),
    )
    annotated: list[dict] = []
    applied: list[dict] = []
    last_emotion = {"name": "Neutral", "intensity": 0.30}

    for raw in messages:
        role = str(raw.get("role") or "bot").strip().lower()
        if role not in {"user", "bot"}:
            role = "bot"
        text, inline_tag = parse_inline_tag(str(raw.get("text") or ""))
        tag = raw.get("tag") or inline_tag
        words = tokenize(text)
        item = {"role": role, "text": text, "words": words, "tag": None}
        result = {
            "role": role,
            "text": text,
            "eliminated": "",
            "cut_space": None,
            "kept_after_tag": "",
            "interrupt": None,
            "emotion": dict(last_emotion),
        }

        if role != "bot" or not tag:
            annotated.append(item)
            applied.append(result)
            continue

        sensor = str(tag.get("sensor") or "").strip()
        value = str(tag.get("value") or "").strip()
        try:
            gap = int(tag.get("gap"))
            left, remainder = split_at_gap(words, gap)
        except (TypeError, ValueError) as exc:
            result["text"] = text
            result["error"] = str(exc)
            item["tag"] = {"gap": tag.get("gap"), "sensor": sensor, "value": value, "error": str(exc)}
            annotated.append(item)
            applied.append(result)
            continue

        tag_text = format_tag(sensor, value)
        item["tag"] = {"gap": gap, "sensor": sensor, "value": value, "display": tag_text}
        item["annotated_text"] = join_words(left + [tag_text] + remainder)
        annotated.append(item)

        keep_count = pick_remainder_cut(remainder, rng)
        kept = remainder[:keep_count]
        eliminated = remainder[keep_count:]
        prefix = join_words(left)
        kept_after = join_words(kept)
        gone = join_words(eliminated)

        try:
            engine.sensors = apply_sensor_value(engine.sensors, sensor, value)
            event = engine.peek(stable=True)
            engine.last_labels = engine.labels()
            if event:
                engine.apply_event(event)
            if engine.sensors.surface_touch:
                engine.sensors.surface_touch = None
                engine.last_labels["surface"] = "none"
        except ValueError as exc:
            result["error"] = str(exc)
            result["text"] = item["annotated_text"]
            applied.append(result)
            continue

        spoken = event.spoken if event else ""
        if event:
            last_emotion = {"name": event.emotion, "intensity": round(event.intensity, 3)}
        pieces = [prefix, tag_text]
        if kept_after:
            pieces.append(kept_after)
        if spoken:
            pieces.append(spoken)
        continuation = ""
        if event and gone:
            try:
                continuation = rewrite_remainder(
                    client,
                    personality,
                    event.emotion,
                    event.intensity,
                    join_words([prefix, kept_after] if kept_after else [prefix]),
                    spoken,
                    gone,
                    engine,
                )
            except Exception:  # noqa: BLE001
                continuation = ""
        if continuation:
            pieces.append(continuation)
        result.update(
            {
                "text": " ".join(part for part in pieces if part).strip(),
                "eliminated": gone,
                "cut_space": keep_count,
                "kept_after_tag": kept_after,
                "interrupt": event_payload(event),
                "emotion": dict(last_emotion),
                "continuation": continuation,
                "annotated_text": item["annotated_text"],
            }
        )
        applied.append(result)

    return {
        "annotated": annotated,
        "applied": applied,
        "emotion": last_emotion,
        "environment": canonical_payload()["environment"],
    }
