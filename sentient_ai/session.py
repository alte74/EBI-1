from __future__ import annotations

import queue
import threading
import time
from collections.abc import Iterator

from sentient_ai.claude_client import ClaudeClient, build_system_prompt
from sentient_ai.emotion import (
    EmotionalState,
    PRIMARY_EMOTIONS,
    default_emotion_map,
    default_thresholds,
    merge_thresholds,
    save_emotion_map,
    save_thresholds,
)
from sentient_ai.experience_kb import ExperienceKB
from sentient_ai.interruption import (
    InterruptionEngine,
    event_payload,
    monitor_interval_seconds,
)
from sentient_ai.personality import Personality
from sentient_ai.sensors import SensoryState, snapshot_labels


class BotSession:
    def __init__(self) -> None:
        self.personality = Personality()
        self.engine = InterruptionEngine(
            personality=self.personality,
            sensors=SensoryState(),
            emotion=EmotionalState(),
        )
        self.kb = ExperienceKB()
        self.history: list[dict] = []
        self.client = ClaudeClient()
        self.draft = self.engine.sensors.copy()
        self.lock = threading.Lock()
        self.streaming = False
        self.force_poll = threading.Event()
        self.stop_stream = threading.Event()

    def public_state(self) -> dict:
        snap = self.engine.snapshot()
        snap["personality"] = self.personality.as_dict()
        snap["draft"] = self.draft.as_dict()
        snap["draft_labels"] = self.engine.labels(self.draft)
        snap["streaming"] = self.streaming
        snap["model"] = self.client.model
        snap["emotion_map"] = self.personality.emotion_map
        snap["thresholds"] = self.personality.thresholds
        snap["primary_emotions"] = list(PRIMARY_EMOTIONS)
        return snap

    def set_draft(self, data: dict) -> dict:
        with self.lock:
            merged = self.draft.as_dict()
            merged.update(data)
            self.draft = SensoryState.from_dict(merged)
        return self.preview_utterances()

    def apply_draft(self, data: dict | None = None, *, defer_poll: bool = False) -> dict:
        with self.lock:
            if data:
                merged = self.draft.as_dict()
                merged.update(data)
                self.draft = SensoryState.from_dict(merged)
            self.engine.sensors = self.draft.copy()
            event = None
            if self.streaming or defer_poll:
                self.force_poll.set()
            else:
                event = self.engine.poll()
            self.draft = self.engine.sensors.copy()
        return {
            "interrupt": event_payload(event),
            "state": self.public_state(),
        }

    def reset_environment(self) -> dict:
        with self.lock:
            if self.streaming:
                self.force_poll.set()
            self.engine.sensors = SensoryState()
            self.engine.last_labels = self.engine.labels()
            self.engine.emotion.set("Neutral", 0.30)
            self.draft = self.engine.sensors.copy()
        return self.public_state()

    def reset_conversation(self) -> dict:
        with self.lock:
            self.history.clear()
        return self.public_state()

    def set_emotion_binding(self, sense: str, band: str, emotion: str) -> dict:
        from sentient_ai.emotion import merge_emotion_map, normalize_emotion

        with self.lock:
            mapping = merge_emotion_map(self.personality.emotion_map)
            if sense not in mapping or band not in mapping[sense]:
                raise ValueError(f"Unknown threshold {sense}/{band}")
            mapping[sense][band] = normalize_emotion(emotion)
            self.personality.emotion_map = mapping
            save_emotion_map(mapping)
        return self.public_state()

    def reset_emotion_map(self) -> dict:
        with self.lock:
            mapping = default_emotion_map()
            self.personality.emotion_map = mapping
            save_emotion_map(mapping)
        return self.public_state()

    def set_threshold(self, sense: str, low: float, high: float) -> dict:
        with self.lock:
            mapping = merge_thresholds(self.personality.thresholds)
            if sense not in mapping:
                raise ValueError(f"Unknown sense: {sense}")
            mapping[sense] = {"low": low, "high": high}
            mapping = merge_thresholds(mapping)
            self.personality.thresholds = mapping
            save_thresholds(mapping)
        return self.public_state()

    def reset_thresholds(self) -> dict:
        with self.lock:
            mapping = default_thresholds()
            self.personality.thresholds = mapping
            save_thresholds(mapping)
        return self.public_state()

    def preview_side(self, sensors: SensoryState) -> dict:
        event = self.engine.peek(sensors, stable=True)
        labels = snapshot_labels(sensors, self.engine.bands())
        name, intensity = self.engine.resulting_emotion(sensors)
        if event:
            utterance = event.spoken
        else:
            utterance = (
                "No interruption. The next reply continues in the current "
                f"{self.engine.emotion.name} tone."
            )
        return {
            "will_interrupt": event is not None,
            "interrupt": event_payload(event),
            "utterance": utterance,
            "emotion": {"name": name, "intensity": round(intensity, 3)},
            "sensors": sensors.as_dict(),
            "labels": labels,
            "reply": None,
        }

    def preview_utterances(self, draft: dict | None = None) -> dict:
        with self.lock:
            if draft:
                merged = self.draft.as_dict()
                merged.update(draft)
                after_sensors = SensoryState.from_dict(merged)
            else:
                after_sensors = self.draft.copy()
            before = self.preview_side(self.engine.sensors.copy())
            after = self.preview_side(after_sensors)
        return {"before": before, "after": after, "state": self.public_state()}

    def preview_replies(self, message: str, draft: dict | None = None) -> dict:
        text = (message or "").strip()
        if not text:
            raise ValueError("Type a message to preview the next utterance.")
        bundle = self.preview_utterances(draft)
        hist = list(self.history) + [{"role": "user", "content": text}]

        before_engine = self._clone_engine(self.engine.sensors.copy())
        bundle["before"]["reply"] = self.client.complete_reply(
            build_system_prompt(self.personality, before_engine.emotion, before_engine, self.kb),
            hist,
        )

        after_sensors = SensoryState.from_dict({**self.draft.as_dict(), **(draft or {})})
        after_engine = self._clone_engine(after_sensors)
        event = after_engine.peek(stable=True)
        if event:
            after_engine.last_labels = after_engine.labels()
            after_engine.emotion.set(event.emotion, event.intensity)
            after_hist = hist + [
                {"role": "assistant", "content": "(cut mid-sentence)--"},
                {
                    "role": "user",
                    "content": (
                        f"[sensory interruption: {event.sense} became {event.band}. "
                        f"You already said out loud: {event.spoken} "
                        f"Your emotion is now {event.emotion}. Continue the previous thought "
                        f"in that tone. Do not repeat the interrupt line.]"
                    ),
                },
            ]
            continuation = self.client.complete_reply(
                build_system_prompt(
                    self.personality, after_engine.emotion, after_engine, self.kb
                ),
                after_hist,
            )
            bundle["after"]["reply"] = continuation
            bundle["after"]["will_interrupt"] = True
            bundle["after"]["utterance"] = event.spoken
            bundle["after"]["interrupt"] = event_payload(event)
            bundle["after"]["emotion"] = {
                "name": event.emotion,
                "intensity": round(event.intensity, 3),
            }
        else:
            bundle["after"]["reply"] = self.client.complete_reply(
                build_system_prompt(
                    self.personality, after_engine.emotion, after_engine, self.kb
                ),
                hist,
            )
        return bundle

    def stream_chat(self, user_text: str) -> Iterator[dict]:
        text = (user_text or "").strip()
        if not text:
            yield {"type": "error", "message": "Empty message."}
            return
        busy = False
        with self.lock:
            if self.streaming:
                busy = True
            else:
                self.streaming = True
                self.stop_stream.clear()
                self.history.append({"role": "user", "content": text})
                system = build_system_prompt(
                    self.personality, self.engine.emotion, self.engine, self.kb
                )
                history = list(self.history)
        if busy:
            yield {"type": "error", "message": "Already speaking."}
            return

        yield {"type": "state", "state": self.public_state()}

        interrupt_at = threading.Event()
        fired: list = []
        stop = threading.Event()

        def monitor() -> None:
            delay = monitor_interval_seconds(self.personality)
            elapsed = 0.0
            step = 0.08
            while not stop.is_set():
                time.sleep(step)
                elapsed += step
                forced = self.force_poll.is_set()
                ready = (forced and elapsed >= 1.0) or elapsed >= delay
                if not ready:
                    continue
                if forced:
                    self.force_poll.clear()
                with self.lock:
                    event = self.engine.poll()
                if event:
                    fired.append(event)
                    interrupt_at.set()
                    return
                delay = elapsed + monitor_interval_seconds(self.personality)

        worker = threading.Thread(target=monitor, daemon=True)
        worker.start()
        token_q: queue.Queue = queue.Queue()

        def on_text(ch: str, allow_interrupt: bool = True) -> bool:
            if self.stop_stream.is_set():
                return False
            if allow_interrupt and interrupt_at.is_set():
                return False
            token_q.put(("token", ch))
            time.sleep(0.012)
            return True

        def producer() -> None:
            try:
                spoken = self.client.stream_reply(system, history, on_text)
                token_q.put(("done", spoken))
            except Exception as exc:  # noqa: BLE001
                token_q.put(("error", str(exc)))

        threading.Thread(target=producer, daemon=True).start()
        spoken = ""
        failed = None
        while True:
            kind, payload = token_q.get()
            if kind == "token":
                spoken += payload
                yield {"type": "token", "text": payload}
            elif kind == "error":
                failed = payload
                break
            else:
                spoken = payload
                break

        stop.set()
        worker.join(timeout=0.4)

        if failed:
            with self.lock:
                self.streaming = False
            yield {"type": "error", "message": failed}
            return

        if fired:
            event = fired[0]
            cut = spoken.rstrip()
            suffix = "--" if cut and cut[-1] not in ".!?" else ""
            if suffix:
                yield {"type": "token", "text": suffix}
            yield {
                "type": "interrupt",
                "interrupt": event_payload(event),
                "cut": cut + suffix,
            }
            yield {"type": "state", "state": self.public_state()}
            with self.lock:
                self.history.append({"role": "assistant", "content": cut + suffix})
                self.history.append(
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
                resume_system = build_system_prompt(
                    self.personality, self.engine.emotion, self.engine, self.kb
                )
                resume_history = list(self.history)
            resume_q: queue.Queue = queue.Queue()

            def resume_on_text(ch: str) -> bool:
                if self.stop_stream.is_set():
                    return False
                resume_q.put(("token", ch))
                time.sleep(0.012)
                return True

            def resume_producer() -> None:
                try:
                    spoken2 = self.client.stream_reply(
                        resume_system, resume_history, resume_on_text, max_tokens=500
                    )
                    resume_q.put(("done", spoken2))
                except Exception as exc:  # noqa: BLE001
                    resume_q.put(("error", str(exc)))

            threading.Thread(target=resume_producer, daemon=True).start()
            spoken2 = ""
            while True:
                kind, payload = resume_q.get()
                if kind == "token":
                    spoken2 += payload
                    yield {"type": "token", "text": payload}
                elif kind == "error":
                    with self.lock:
                        self.streaming = False
                    yield {"type": "error", "message": payload}
                    return
                else:
                    spoken2 = payload
                    break
            with self.lock:
                self.history.append(
                    {"role": "assistant", "content": event.spoken + " " + spoken2}
                )
        else:
            with self.lock:
                self.history.append({"role": "assistant", "content": spoken})

        with self.lock:
            self.streaming = False
        yield {"type": "state", "state": self.public_state()}
        yield {"type": "done"}

    def _clone_engine(self, sensors: SensoryState) -> InterruptionEngine:
        return InterruptionEngine(
            personality=self.personality,
            sensors=sensors,
            emotion=EmotionalState(
                name=self.engine.emotion.name,
                intensity=self.engine.emotion.intensity,
            ),
            last_labels=dict(self.engine.last_labels),
        )
