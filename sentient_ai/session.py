from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor

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
from sentient_ai.memory import MemoryStore
from sentient_ai.personality import Personality
from sentient_ai.sensors import SensoryState, snapshot_labels
from sentient_ai.simulation import canonical_payload, simulate_conversation
from sentient_ai.speech import SpeechRecognizer
from sentient_ai.vision_agent import VisionAgent, VisionReport, calibrated_lumens
from sentient_ai.voice import (
    SentenceSplitter,
    Speech,
    SynthJob,
    VoiceSynth,
    VoiceUnavailable,
    reaction_pieces,
)

log = logging.getLogger(__name__)

LUMENS_CHANGE = 0.02


class BotSession:
    """The Cognitive Agent: converses, feels, and listens to the Vision Agent."""

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
        self.vision_drives_light = True
        self.camera_lumens: float | None = None
        self.lumens_seq = 0
        self.last_scene: dict | None = None
        self.memory = MemoryStore()
        self.vision = VisionAgent(self.client, self.receive_vision)
        self.speech = SpeechRecognizer()
        self.voice = VoiceSynth()

    def _remember(self, kind: str, text: str, **fields) -> None:
        if not (text or "").strip() and fields.get("image") is None:
            return
        try:
            self.memory.add(kind, text, **fields)
        except OSError:
            log.exception("Could not save %s memory", kind)

    def _remember_chat(self, role: str, text: str) -> None:
        self._remember("chat", text, role=role)

    def receive_vision(self, report: VisionReport, jpeg: bytes | None) -> None:
        """Called from the Vision Agent thread on every snapshot."""
        lumens = None
        with self.lock:
            if report.room_type != "unknown":
                self.last_scene = report.as_dict()
            judged = report.luminance is not None and report.lighting != "unknown"
            if judged and self.vision_drives_light:
                lumens = calibrated_lumens(
                    report.luminance, report.lighting, self.engine.bands().vision
                )
                self._follow_camera_light(lumens)
        if jpeg is not None:
            self._remember(
                "vision",
                report.summary or report.error or "",
                image=jpeg,
                meta={
                    "seq": report.seq,
                    "room_type": report.room_type,
                    "people": report.people,
                    "room_properties": report.room_properties,
                    "lighting": report.lighting,
                    "luminance": report.as_dict().get("luminance"),
                    "lumens": lumens,
                },
            )

    def _follow_camera_light(self, lumens: float) -> None:
        """Move Lumens (live and draft) when the latest picture's light differs from the last one."""
        previous = self.camera_lumens
        if previous is not None and abs(lumens - previous) < LUMENS_CHANGE:
            return
        self.camera_lumens = lumens
        self.lumens_seq += 1
        self.draft.set_continuous("light", lumens)
        self.engine.sensors.set_continuous("light", lumens)
        crossed = self.engine.labels()["vision"] != self.engine.last_labels.get("vision")
        if crossed and self.streaming:
            self.force_poll.set()

    def vision_state(self) -> dict:
        state = self.vision.snapshot()
        state["drive_light"] = self.vision_drives_light
        state["light_band"] = self.engine.labels()["vision"]
        state["lumens"] = self.camera_lumens
        state["lumens_seq"] = self.lumens_seq
        return state

    def set_vision(
        self,
        enabled: bool | None = None,
        drive_light: bool | None = None,
        interval: float | None = None,
    ) -> dict:
        if interval is not None:
            self.vision.set_interval(interval)
        if enabled is not None:
            self.vision.set_enabled(enabled)
        if drive_light is not None:
            with self.lock:
                if drive_light and not self.vision_drives_light:
                    self.camera_lumens = None
                self.vision_drives_light = bool(drive_light)
        return self.public_state()

    def _scene(self) -> dict | None:
        return self.last_scene if self.vision.enabled else None

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
        snap["vision"] = self.vision_state()
        snap["speech"] = self.speech.snapshot()
        snap["voice"] = self.voice.snapshot()
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
        if event:
            self._remember_chat("interrupt", event.spoken)
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
            self.engine.last_event = None
            self.draft = self.engine.sensors.copy()
            self.camera_lumens = None
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
            build_system_prompt(
                self.personality, before_engine.emotion, before_engine, self.kb, self._scene()
            ),
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
                    self.personality, after_engine.emotion, after_engine, self.kb, self._scene()
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
                    self.personality, after_engine.emotion, after_engine, self.kb, self._scene()
                ),
                hist,
            )
        return bundle

    def stream_chat(self, user_text: str, voice: dict | None = None) -> Iterator[dict]:
        """Stream a reply. With voice settings the bot also speaks it, see _spoken_reply."""
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
                    self.personality, self.engine.emotion, self.engine, self.kb, self._scene()
                )
                history = list(self.history)
        if busy:
            yield {"type": "error", "message": "Already speaking."}
            return

        self._remember_chat("user", text)
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
        settings = self.voice.settings(voice)
        spoken, failed = yield from self._reply(
            system,
            history,
            settings,
            halt=lambda: self.stop_stream.is_set() or interrupt_at.is_set(),
        )

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
            self._remember_chat("bot", cut + suffix)
            self._remember_chat("interrupt", event.spoken)
            with self.lock:
                self.history.append({"role": "assistant", "content": cut + suffix or "--"})
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
                    self.personality, self.engine.emotion, self.engine, self.kb, self._scene()
                )
                resume_history = list(self.history)
            voice_free = time.monotonic()
            if settings:
                voice_free = yield from self._say_reaction(event.spoken, settings)
            spoken2, failed = yield from self._reply(
                resume_system,
                resume_history,
                settings,
                halt=self.stop_stream.is_set,
                max_tokens=500,
                voice_free=voice_free,
            )
            if failed:
                with self.lock:
                    self.streaming = False
                yield {"type": "error", "message": failed}
                return
            self._remember_chat("bot", spoken2)
            with self.lock:
                self.history.append(
                    {"role": "assistant", "content": event.spoken + " " + spoken2}
                )
        else:
            self._remember_chat("bot", spoken)
            with self.lock:
                self.history.append({"role": "assistant", "content": spoken or "--"})

        with self.lock:
            self.streaming = False
        yield {"type": "state", "state": self.public_state()}
        yield {"type": "done"}

    def say(self, text: str, voice: dict | None) -> Speech | None:
        """Speak one line outside a reply (voice test, interrupt while idle)."""
        settings = self.voice.settings(voice)
        if settings is None:
            raise VoiceUnavailable(self.voice.unavailable())
        return self.voice.synthesize(
            text, settings["voice"], settings["speed"], self._voice_emotion(settings)
        )

    def _voice_emotion(self, settings: dict) -> tuple[str, float] | None:
        if not settings["emotion"]:
            return None
        with self.lock:
            return self.engine.emotion.name, self.engine.emotion.intensity

    def _speak_piece(self, text: str, settings: dict, job: SynthJob | None = None) -> Speech | None:
        try:
            return self.voice.synthesize(
                text, settings["voice"], settings["speed"], self._voice_emotion(settings), job
            )
        except Exception:  # noqa: BLE001
            log.exception("Could not speak %r", text)
            return None

    def _say_reaction(self, line: str, settings: dict) -> Iterator[dict]:
        """Speak an interruption line; returns when the voice will have finished it."""
        voice_free = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(self._speak_piece, piece, settings) for piece in reaction_pieces(line)]
            for job in jobs:
                speech = job.result()
                if speech is not None:
                    yield self._audio_event(speech)
                    voice_free = max(voice_free, time.monotonic()) + speech.seconds
        return voice_free

    @staticmethod
    def _audio_event(speech: Speech) -> dict:
        return {"type": "audio", "audio": speech.pcm_base64(), "rate": speech.rate}

    def _reply(
        self,
        system: str,
        history: list[dict],
        voice: dict | None,
        *,
        halt,
        max_tokens: int = 600,
        voice_free: float = 0.0,
    ) -> Iterator[dict]:
        """Stream one Claude reply as token events; returns (text, error).

        halt() says when to stop mid-sentence. voice_free is when the voice finishes
        what it is already saying.
        """
        if voice is None:
            return (yield from self._typed_reply(system, history, halt, max_tokens))
        return (yield from self._spoken_reply(system, history, voice, halt, max_tokens, voice_free))

    def _typed_reply(self, system, history, halt, max_tokens) -> Iterator[dict]:
        token_q: queue.Queue = queue.Queue()

        def on_text(ch: str) -> bool:
            if halt():
                return False
            token_q.put(("token", ch))
            time.sleep(0.012)
            return True

        def producer() -> None:
            try:
                spoken = self.client.stream_reply(system, history, on_text, max_tokens=max_tokens)
                token_q.put(("done", spoken))
            except Exception as exc:  # noqa: BLE001
                token_q.put(("error", str(exc)))

        threading.Thread(target=producer, daemon=True).start()
        while True:
            kind, payload = token_q.get()
            if kind == "token":
                yield {"type": "token", "text": payload}
            elif kind == "error":
                return "", payload
            else:
                return payload, None

    def _spoken_reply(self, system, history, voice, halt, max_tokens, voice_free) -> Iterator[dict]:
        """Speak the reply as Claude writes it, showing each word as the voice reaches it.

        One thread reads Claude and cuts the text into pieces, another synthesizes each
        piece while the one before it plays. The text on screen is what has been said
        out loud, so an interruption cuts the voice and the text at the same word.
        """
        pieces: queue.Queue = queue.Queue()
        ready: queue.Queue = queue.Queue(maxsize=2)
        finished = threading.Event()
        splitter = SentenceSplitter()
        shown: list[str] = []

        def stopped() -> bool:
            # Nobody can be cut off before saying a word; a closed page stops at once.
            return halt() and (bool(shown) or self.stop_stream.is_set())

        def on_text(ch: str) -> bool:
            if finished.is_set() or (halt() and splitter.count):
                return False
            for piece in splitter.feed(ch):
                pieces.put(piece)
            return True

        def reader() -> None:
            try:
                self.client.stream_reply(system, history, on_text, max_tokens=max_tokens)
            except Exception as exc:  # noqa: BLE001
                pieces.put(exc)
            else:
                tail = splitter.flush()
                if tail:
                    pieces.put(tail)
            pieces.put(None)

        def hand_over(item) -> None:
            while not finished.is_set():
                try:
                    ready.put(item, timeout=0.1)
                    return
                except queue.Full:
                    continue

        jobs: list[SynthJob] = []

        def synthesizer() -> None:
            while not finished.is_set():
                item = pieces.get()
                if not isinstance(item, str):
                    hand_over(item)
                    return
                job = SynthJob()
                jobs.append(job)
                if finished.is_set():
                    return
                hand_over((item, self._speak_piece(item, voice, job)))

        def wait_until(moment: float) -> bool:
            while not stopped():
                left = moment - time.monotonic()
                if left <= 0:
                    return True
                time.sleep(min(0.03, left))
            return False

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=synthesizer, daemon=True).start()
        voice_free = max(voice_free, time.monotonic())
        try:
            while True:
                try:
                    item = ready.get(timeout=0.05)
                except queue.Empty:
                    if stopped():
                        return "".join(shown), None
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    return "".join(shown), str(item)
                text, speech = item
                start = max(voice_free, time.monotonic())
                marks = [(len(text), 0.0)]
                if speech is not None:
                    yield self._audio_event(speech)
                    marks = speech.marks
                    voice_free = start + speech.seconds
                said = 0
                for chars, at in marks + [(len(text), 0.0)]:
                    if not wait_until(start + at):
                        return "".join(shown), None
                    if chars > said:
                        shown.append(text[said:chars])
                        yield {"type": "token", "text": text[said:chars]}
                        said = chars
            wait_until(voice_free)
            return "".join(shown), None
        finally:
            # Stop synthesizing ahead so an interruption line gets the whole CPU.
            finished.set()
            for job in jobs:
                job.cancel()

    def simulation_canonical(self) -> dict:
        return canonical_payload()

    def apply_simulation(self, messages: list[dict]) -> dict:
        if not messages:
            raise ValueError("Simulation needs a conversation to apply.")
        return simulate_conversation(
            messages,
            personality=self.personality,
            client=self.client,
        )

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
