from __future__ import annotations

import sys
import threading
import time

from sentient_ai.claude_client import ClaudeClient, build_system_prompt
from sentient_ai.emotion import EmotionalState
from sentient_ai.experience_kb import ExperienceKB
from sentient_ai.interruption import FLOW, InterruptionEngine, monitor_interval_seconds
from sentient_ai.personality import Personality
from sentient_ai.sensors import SensoryState

HELP = """
Commands
  /help                 this list
  /status               emotion + sensors
  /emotion              current emotional state
  /personality          personality sliders
  /flow                 Emotion by Interruption diagram
  /set temp 0.85        temperature 0..1
  /set light 0.20       luminosity 0..1
  /set volume 0.90      hearing 0..1
  /set humidity 0.80    humidity 0..1
  /smell none|rotten|flowers
  /neutral              reset sensors to comfortable
  /demo heat|cold|dark|bright|loud|quiet|rotten|flowers
  /quit                 exit

Anything else is conversation. Change a sensor while talking by using /demo,
or set a value first — the next reply can be cut mid-sentence when a threshold is crossed.
"""

DEMO = {
    "heat": ("temperature", 0.92),
    "cold": ("temperature", 0.08),
    "dark": ("light", 0.10),
    "bright": ("light", 0.95),
    "loud": ("volume", 0.95),
    "quiet": ("volume", 0.05),
}


def run_chat() -> None:
    personality = Personality()
    engine = InterruptionEngine(
        personality=personality,
        sensors=SensoryState(),
        emotion=EmotionalState(),
    )
    kb = ExperienceKB()
    history: list[dict] = []
    client = ClaudeClient()

    print("Sentient AI  v1.0  -- Emotion by Interruption")
    print(f"Model: {client.model}   Individual: {personality.individual_id}")
    print("Type /help for commands. Type /flow to see the interrupt sequence.")
    print(engine.hud())
    print()

    while True:
        try:
            raw = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return
        if not raw:
            continue
        if raw.startswith("/"):
            if not _handle_command(raw, engine, personality):
                return
            continue

        pending_demo = _pending_pop()
        _converse(client, engine, personality, kb, history, raw, pending_demo)


_pending: dict | None = None


def _pending_pop() -> dict | None:
    global _pending
    item = _pending
    _pending = None
    return item


def _handle_command(raw: str, engine: InterruptionEngine, personality: Personality) -> bool:
    global _pending
    parts = raw.split()
    cmd = parts[0].lower()

    if cmd in {"/quit", "/exit"}:
        print("bye.")
        return False
    if cmd == "/help":
        print(HELP)
        return True
    if cmd == "/status":
        print(engine.hud())
        return True
    if cmd == "/emotion":
        print(f"Current Emotional State: {engine.emotion.name} ({engine.emotion.intensity:.2f})")
        return True
    if cmd == "/personality":
        print(personality.as_prompt())
        return True
    if cmd == "/flow":
        print(FLOW)
        return True
    if cmd == "/neutral":
        engine.sensors = SensoryState()
        engine.last_labels = engine.labels()
        engine.emotion.set("Neutral", 0.30)
        print(engine.hud())
        return True
    if cmd == "/smell":
        if len(parts) < 2:
            print("usage: /smell none|rotten|flowers")
            return True
        try:
            engine.sensors.set_smell(parts[1])
        except ValueError as exc:
            print(exc)
            return True
        print("(queued for next reply if it crosses a threshold)")
        print(engine.hud())
        return True
    if cmd == "/set":
        if len(parts) != 3:
            print("usage: /set temp|light|volume|humidity 0.0-1.0")
            return True
        name = {"temp": "temperature", "temperature": "temperature"}.get(parts[1], parts[1])
        try:
            value = float(parts[2])
            engine.sensors.set_continuous(name, value)
        except ValueError as exc:
            print(exc)
            return True
        print("(queued for next reply if it crosses a threshold)")
        print(engine.hud())
        return True
    if cmd == "/demo":
        if len(parts) != 2:
            print("usage: /demo heat|cold|dark|bright|loud|quiet|rotten|flowers")
            return True
        kind = parts[1].lower()
        if kind in {"rotten", "flowers"}:
            _pending = {"smell": kind}
        elif kind in DEMO:
            attr, value = DEMO[kind]
            _pending = {"sensor": attr, "value": value}
        else:
            print("unknown demo")
            return True
        print(f"(queued: {kind} will hit mid-reply)")
        return True

    print("unknown command — /help")
    return True


def _converse(
    client: ClaudeClient,
    engine: InterruptionEngine,
    personality: Personality,
    kb: ExperienceKB,
    history: list[dict],
    user_text: str,
    pending_demo: dict | None,
) -> None:
    history.append({"role": "user", "content": user_text})
    system = build_system_prompt(personality, engine.emotion, engine, kb)

    stop = threading.Event()
    interrupt_at = threading.Event()
    fired: list = []

    def monitor() -> None:
        already_shifted = engine.labels() != engine.last_labels
        if pending_demo or already_shifted:
            delay = 1.4
        else:
            delay = monitor_interval_seconds(personality)
        elapsed = 0.0
        step = 0.1
        demo_applied = False
        while not stop.is_set():
            time.sleep(step)
            elapsed += step
            if pending_demo and not demo_applied and elapsed >= 1.2:
                if "smell" in pending_demo:
                    engine.sensors.set_smell(pending_demo["smell"])
                else:
                    engine.sensors.set_continuous(pending_demo["sensor"], pending_demo["value"])
                demo_applied = True
            if elapsed >= delay:
                event = engine.poll()
                if event:
                    fired.append(event)
                    interrupt_at.set()
                    return
                delay = elapsed + monitor_interval_seconds(personality)

    worker = threading.Thread(target=monitor, daemon=True)
    worker.start()

    print("bot> ", end="", flush=True)

    def on_text(ch: str, allow_interrupt: bool = True) -> bool:
        if allow_interrupt and interrupt_at.is_set():
            return False
        sys.stdout.write(ch)
        sys.stdout.flush()
        time.sleep(0.012)
        return True

    spoken = client.stream_reply(system, history, on_text)
    stop.set()
    worker.join(timeout=0.2)

    if fired:
        event = fired[0]
        cut = spoken.rstrip()
        if cut and cut[-1] not in ".!?":
            sys.stdout.write("--")
        sys.stdout.write("\n\n")
        sys.stdout.write(event.spoken)
        sys.stdout.write("\n\n")
        sys.stdout.flush()

        history.append({"role": "assistant", "content": cut + "--"})
        history.append(
            {
                "role": "user",
                "content": (
                    f"[sensory interruption: {event.sense} became {event.band}. "
                    f"You already said out loud: {event.spoken} "
                    f"Your emotion is now {event.emotion}. Continue the previous thought "
                    f"in that tone. Do not repeat the interrupt line.]"
                ),
            }
        )
        resume_system = build_system_prompt(personality, engine.emotion, engine, kb)
        print("bot> ", end="", flush=True)
        spoken2 = client.stream_reply(
            resume_system, history, lambda ch: on_text(ch, allow_interrupt=False)
        )
        print()
        history.append({"role": "assistant", "content": event.spoken + " " + spoken2})
    else:
        print()
        history.append({"role": "assistant", "content": spoken})

    print(engine.hud())
    print()
