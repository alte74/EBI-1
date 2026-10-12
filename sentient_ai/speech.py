from __future__ import annotations

import io
import logging
import threading
import time
import wave

import numpy as np

from sentient_ai.config import speech_enabled, speech_model, speech_quantization

log = logging.getLogger(__name__)

SAMPLE_RATES = (8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000)
MAX_SECONDS = 180


class SpeechUnavailable(RuntimeError):
    pass


def decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Return (mono float32 samples in -1..1, sample rate) for a 16-bit PCM WAV file."""
    try:
        with wave.open(io.BytesIO(data)) as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError("Speech must be sent as a WAV file.") from exc
    if width != 2:
        raise ValueError("Speech must be 16-bit PCM.")
    if rate not in SAMPLE_RATES:
        raise ValueError(f"Unsupported sample rate: {rate} Hz.")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


class SpeechRecognizer:
    """Speech-to-text for the chat: NVIDIA Parakeet TDT, run locally on the CPU by onnx-asr.

    The model loads on a background thread so the server starts at once. The first
    start downloads it from Hugging Face; later starts load it from the local cache.
    """

    def __init__(self) -> None:
        self.enabled = speech_enabled()
        self.model_name = speech_model()
        self.quantization = speech_quantization()
        self.status = "stopped" if self.enabled else "off"
        self.error: str | None = None
        self._model = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self.status = "loading"
        self._thread = threading.Thread(target=self._load, name="speech-model", daemon=True)
        self._thread.start()

    def _load(self) -> None:
        try:
            import onnx_asr
        except ImportError:
            self._fail("onnx-asr is not installed. Run: pip install -r requirements.txt")
            return
        try:
            self._model = onnx_asr.load_model(self.model_name, quantization=self.quantization)
        except Exception as exc:  # noqa: BLE001
            log.exception("Could not load speech model %s", self.model_name)
            self._fail(f"Could not load speech model {self.model_name}: {exc}")
            return
        self.status = "ready"

    def _fail(self, message: str) -> None:
        self.error = message
        self.status = "error"

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "status": self.status,
            "model": self.model_name,
            "error": self.error,
        }

    def transcribe(self, wav: bytes) -> dict:
        model = self._model
        if model is None:
            if self.status == "off":
                raise SpeechUnavailable("Speech-to-text is turned off (SPEECH_TO_TEXT in .env).")
            raise SpeechUnavailable(self.error or "The speech model is still loading. Try again in a moment.")
        samples, rate = decode_wav(wav)
        seconds = len(samples) / rate
        if seconds > MAX_SECONDS:
            raise ValueError(f"Speak for at most {MAX_SECONDS} seconds at a time.")
        started = time.perf_counter()
        with self._lock:
            text = model.recognize(samples, sample_rate=rate)
        return {
            "text": text.strip(),
            "seconds": round(seconds, 2),
            "elapsed": round(time.perf_counter() - started, 2),
        }
