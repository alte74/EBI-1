from __future__ import annotations

import base64
import io
import logging
import re
import shutil
import threading
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sentient_ai.config import voice_default, voice_enabled

log = logging.getLogger(__name__)

MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
# The v1.1 export reports phoneme durations, which is what times the words to the voice.
MODEL_DIR = Path.home() / ".cache" / "kokoro-onnx" / "model-files-v1.1"
MODEL_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")

VOICES = (
    ("am_michael", "Michael · American man, about 47"),
    ("bm_george", "George · British man, about 52"),
    ("bm_lewis", "Lewis · British man, about 52"),
    ("am_fenrir", "Fenrir · American man, about 37"),
    ("am_puck", "Puck · American man, about 30"),
    ("af_heart", "Heart · American woman, about 32"),
    ("af_bella", "Bella · American woman, about 38"),
    ("bf_emma", "Emma · British woman, about 31"),
)

MIN_SPEED, MAX_SPEED = 0.5, 2.0

# (speaking rate, loudness) at full intensity; weaker emotions move part of the way.
EMOTION_STYLE = {
    "Neutral": (1.0, 1.0),
    "Joy": (1.08, 1.05),
    "Sadness": (0.86, 0.8),
    "Fear": (1.1, 0.85),
    "Anger": (1.1, 1.25),
    "Surprise": (1.08, 1.1),
    "Disgust": (0.94, 0.95),
}


class VoiceUnavailable(RuntimeError):
    pass


class SynthJob:
    """One synthesis that another thread can cancel while it runs."""

    def __init__(self) -> None:
        import onnxruntime

        self.options = onnxruntime.RunOptions()
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True
        self.options.terminate = True


class _CancellableSession:
    """The model's onnxruntime session, running each call with the thread's SynthJob."""

    def __init__(self, session) -> None:
        self._session = session
        self.local = threading.local()

    def __getattr__(self, name):
        return getattr(self._session, name)

    def run(self, output_names, inputs):
        job = getattr(self.local, "job", None)
        return self._session.run(output_names, inputs, job.options if job else None)


def emotion_style(name: str, intensity: float) -> tuple[float, float]:
    rate, loud = EMOTION_STYLE.get(name, (1.0, 1.0))
    return 1 + (rate - 1) * intensity, 1 + (loud - 1) * intensity


# Sounds the phonemizer would spell out letter by letter ("Mm" becomes "em em").
SOUNDS = (
    (r"\*yawn\*|\bh+a+h+\b", "Haah"),
    (r"\bm{2,}\b", "Hmm"),
    (r"\bpff+t*\b[,.]?\s*", ""),
)


def speakable(text: str) -> str:
    """The text as it should be read aloud: no markdown, links or dashes."""
    for pattern, sound in SOUNDS:
        text = re.sub(pattern, sound, text, flags=re.IGNORECASE)
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_#`~>|\[\]]", "", text)
    text = re.sub(r"\s*(?:—|–|--+)\s*", ", ", text)
    return " ".join(text.split()).strip(" ,")


@dataclass
class Speech:
    samples: np.ndarray
    rate: int
    marks: list[tuple[int, float]]
    """(characters of the text to show, seconds into the audio), one per word."""

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.rate

    def pcm_base64(self) -> str:
        return base64.b64encode(self.samples.tobytes()).decode("ascii")

    def wav(self) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(self.rate)
            out.writeframes(self.samples.tobytes())
        return buf.getvalue()


def word_marks(text: str, timings: list, seconds: float) -> list[tuple[int, float]]:
    """Show each word of `text` when the voice starts saying it."""
    words = list(re.finditer(r"\S+\s*", text))
    if not words:
        return [(len(text), 0.0)]
    starts: list[float] = []
    inside = False
    for timing in timings:
        if timing.phoneme.isspace():
            inside = False
        elif not inside:
            inside = True
            starts.append(timing.start)
    if len(starts) != len(words):
        starts = [seconds * word.start() / len(text) for word in words]
    return [(word.end(), at) for word, at in zip(words, starts)]


def reaction_pieces(line: str) -> list[str]:
    """An interruption line in two pieces: the quick "Oh!" first, then the comment."""
    first, _, rest = line.partition(" ")
    return [first + " ", rest] if len(rest.split()) >= 2 else [line]


class SentenceSplitter:
    """Cuts streamed text into pieces to synthesize.

    The first piece is short so the bot starts talking quickly. Later pieces are whole
    sentences, bundled to at least MIN characters because the model mumbles very short
    lines, and broken at a comma or space when a sentence runs past MAX.
    """

    FIRST_MIN = 24
    MIN = 60
    MAX = 220

    def __init__(self) -> None:
        self.buf = ""
        self.count = 0

    def feed(self, text: str) -> list[str]:
        self.buf += text
        if not self.buf[-1:].isspace() or not self._boundary():
            return []
        piece, self.buf = self.buf, ""
        self.count += 1
        return [piece]

    def flush(self) -> str:
        piece, self.buf = self.buf, ""
        return piece if piece.strip() else ""

    def _boundary(self) -> bool:
        body = self.buf.rstrip()
        if not body:
            return False
        least = self.FIRST_MIN if self.count == 0 else self.MIN
        end = body.rstrip("\"')]”’")[-1:] or " "
        if (end in ".!?…" or self.buf.endswith("\n")) and len(body) >= least:
            return True
        if end in ",;:—" and len(body) >= (self.FIRST_MIN if self.count == 0 else self.MAX * 0.6):
            return True
        return len(body) >= self.MAX


class VoiceSynth:
    """The bot's voice: Kokoro-82M, run locally on the CPU by kokoro-onnx.

    The model loads on a background thread so the server starts at once. The first
    start downloads it (about 340 MB); later starts load it from the cache.
    """

    def __init__(self) -> None:
        self.enabled = voice_enabled()
        self.default = voice_default()
        self.status = "stopped" if self.enabled else "off"
        self.error: str | None = None
        self.voices = [voice for voice, _ in VOICES]
        self._labels = dict(VOICES)
        self._model = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self.status = "loading"
        self._thread = threading.Thread(target=self._load, name="voice-model", daemon=True)
        self._thread.start()

    def _load(self) -> None:
        try:
            from kokoro_onnx import Kokoro
        except ImportError:
            self._fail("kokoro-onnx is not installed. Run: pip install -r requirements.txt")
            return
        try:
            model_path, voices_path = (self._fetch(name) for name in MODEL_FILES)
            self.status = "loading"
            model = Kokoro(str(model_path), str(voices_path))
            model.sess = _CancellableSession(model.sess)
            available = set(model.get_voices())
            self.voices = [voice for voice in self.voices if voice in available]
            if self.default not in available:
                self.default = self.voices[0]
            # The first inference is slow; pay for it now instead of on the first reply.
            model.create("Hello.", self.default)
        except Exception as exc:  # noqa: BLE001
            log.exception("Could not load the voice model")
            self._fail(f"Could not load the voice model: {exc}")
            return
        self._model = model
        self.status = "ready"

    def _fetch(self, name: str) -> Path:
        path = MODEL_DIR / name
        if path.exists():
            return path
        self.status = "downloading"
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        with urllib.request.urlopen(MODEL_URL + name, timeout=60) as response, part.open("wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)
        part.replace(path)
        return path

    def _fail(self, message: str) -> None:
        self.error = message
        self.status = "error"

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "error": self.error,
            "default": self.default,
            "voices": [{"id": voice, "label": self._labels[voice]} for voice in self.voices],
        }

    def unavailable(self) -> str:
        if self.status == "off":
            return "The bot's voice is turned off (TEXT_TO_SPEECH in .env)."
        return self.error or "The voice model is still loading. Try again in a moment."

    def settings(self, raw: dict | None) -> dict | None:
        """Voice settings sent by the page, checked; None means the bot only types."""
        if not raw or self._model is None:
            return None
        voice = str(raw.get("voice") or self.default)
        try:
            speed = float(raw.get("speed") or 1.0)
        except (TypeError, ValueError):
            speed = 1.0
        return {
            "voice": voice if voice in self.voices else self.default,
            "speed": min(MAX_SPEED, max(MIN_SPEED, speed)),
            "emotion": bool(raw.get("emotion", True)),
        }

    def synthesize(
        self,
        text: str,
        voice: str,
        speed: float = 1.0,
        emotion: tuple[str, float] | None = None,
        job: SynthJob | None = None,
    ) -> Speech | None:
        """Speak `text`; None when there is nothing in it to say or the job was cancelled."""
        model = self._model
        if model is None:
            raise VoiceUnavailable(self.unavailable())
        spoken = speakable(text)
        if not re.search(r"\w", spoken):
            return None
        rate, loud = emotion_style(*emotion) if emotion else (1.0, 1.0)
        speed = min(MAX_SPEED, max(MIN_SPEED, speed * rate))
        lang = "en-gb" if voice.startswith("b") else "en-us"
        # No lock: onnxruntime runs a session from several threads at once.
        model.sess.local.job = job
        try:
            audio, sample_rate, timings = model.create_timed(spoken, voice, speed=speed, lang=lang)
        except Exception:
            if job is not None and job.cancelled:
                return None
            raise
        finally:
            model.sess.local.job = None
        audio = np.asarray(audio, dtype=np.float32) * loud
        peak = float(np.abs(audio).max(initial=0.0))
        if peak > 0.98:
            audio *= 0.98 / peak
        samples = (audio * 32767).astype("<i2")
        seconds = len(samples) / sample_rate
        return Speech(samples, sample_rate, word_marks(text, timings, seconds))
