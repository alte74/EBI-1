from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from sentient_ai.claude_client import ClaudeClient
from sentient_ai.config import (
    vision_camera_index,
    vision_enabled,
    vision_interval_seconds,
    vision_model,
)

log = logging.getLogger(__name__)

LIGHTING_VERDICTS = ("too_dim", "normal", "too_bright")
FRAME_MAX_WIDTH = 640
WARMUP_FRAMES = 12
FLUSH_FRAMES = 4

VISION_SYSTEM = (
    "You are the Vision Agent of a sentient-style robot. You look through its camera "
    "and report to its Cognitive Agent. Be concrete and brief. Reply with JSON only."
)

VISION_PROMPT = """Look at this camera frame and report what you see.

Measured mean pixel brightness of the frame: {luminance:.2f} (0 = black, 1 = white).
Webcams auto-adjust exposure, so also judge lighting from visual cues: grain/noise,
deep shadows, blown-out highlights, visible lamps, windows, or daylight.

Return exactly this JSON object and nothing else:
{{
  "room_type": "short name of the room or place, e.g. home office, kitchen, bedroom",
  "people": ["one short description per visible person: apparent age range, clothing, expression, activity"],
  "room_properties": ["3-6 short notable properties: furniture, objects, colors, tidiness, windows"],
  "lighting": "too_dim | normal | too_bright",
  "lighting_notes": "one sentence on the light level and where it comes from",
  "summary": "one sentence, first person, as if the robot is noticing its surroundings"
}}

Use an empty list for people if nobody is visible. Never guess anyone's name or identity."""


class CameraUnavailable(RuntimeError):
    pass


@dataclass
class VisionReport:
    seq: int
    captured_at: float
    luminance: float | None = None
    room_type: str = "unknown"
    people: list[str] = field(default_factory=list)
    room_properties: list[str] = field(default_factory=list)
    lighting: str = "unknown"
    lighting_notes: str = ""
    summary: str = ""
    error: str | None = None

    def as_dict(self) -> dict:
        data = asdict(self)
        if self.luminance is not None:
            data["luminance"] = round(self.luminance, 3)
        return data


class Camera:
    """Default system camera, held open so auto-exposure stays settled between shots."""

    def __init__(self, index: int) -> None:
        self.index = index
        self._cap = None

    def _open(self):
        try:
            import cv2
        except ImportError as exc:
            raise CameraUnavailable(
                "OpenCV is not installed. Run: pip install opencv-python"
            ) from exc
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            cap.release()
            raise CameraUnavailable(f"No camera found at index {self.index}.")
        for _ in range(WARMUP_FRAMES):
            cap.read()
        return cap

    def capture(self) -> tuple[bytes, float]:
        """Return (jpeg bytes, mean luminance 0..1) for a fresh frame."""
        import cv2

        if self._cap is None:
            self._cap = self._open()
        # Drivers buffer a few frames; drop them so the shot is current.
        for _ in range(FLUSH_FRAMES):
            self._cap.grab()
        ok, frame = self._cap.read()
        if not ok or frame is None:
            self.release()
            raise CameraUnavailable("Camera stopped returning frames.")

        height, width = frame.shape[:2]
        if width > FRAME_MAX_WIDTH:
            scale = FRAME_MAX_WIDTH / width
            frame = cv2.resize(frame, (FRAME_MAX_WIDTH, int(height * scale)))
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        luminance = float(gray.mean()) / 255.0
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            raise CameraUnavailable("Could not encode camera frame.")
        return buf.tobytes(), luminance

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


def _parse_report(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("Vision model did not return JSON.")
    data = json.loads(text[start : end + 1])
    lighting = str(data.get("lighting", "")).strip().lower().replace(" ", "_")
    if lighting not in LIGHTING_VERDICTS:
        lighting = "unknown"
    return {
        "room_type": str(data.get("room_type") or "unknown"),
        "people": [str(p) for p in data.get("people") or []],
        "room_properties": [str(p) for p in data.get("room_properties") or []],
        "lighting": lighting,
        "lighting_notes": str(data.get("lighting_notes") or ""),
        "summary": str(data.get("summary") or ""),
    }


class VisionAgent:
    """Background agent: every interval, photograph the room and report to the Cognitive Agent."""

    def __init__(
        self,
        client: ClaudeClient,
        on_report: Callable[[VisionReport], None],
        *,
        camera_index: int | None = None,
        interval: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.client = client
        self.on_report = on_report
        self.camera = Camera(vision_camera_index() if camera_index is None else camera_index)
        self.interval = vision_interval_seconds() if interval is None else interval
        self.model = vision_model()
        self.enabled = vision_enabled() if enabled is None else enabled
        self.status = "stopped"
        self._latest: VisionReport | None = None
        self._frame: bytes | None = None
        self._seq = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision-agent", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.camera.release()
        self.status = "stopped"

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)
        self._wake.set()

    def latest(self) -> VisionReport | None:
        with self._lock:
            return self._latest

    def frame(self) -> bytes | None:
        with self._lock:
            return self._frame

    def snapshot(self) -> dict:
        report = self.latest()
        return {
            "enabled": self.enabled,
            "status": self.status,
            "interval": self.interval,
            "model": self.model,
            "report": report.as_dict() if report else None,
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            if self.enabled:
                self.status = "watching"
                self._tick()
            else:
                self.status = "paused"
                self.camera.release()
            remaining = self.interval - (time.monotonic() - started)
            self._wake.wait(max(0.5, remaining))
            self._wake.clear()

    def _tick(self) -> None:
        self._seq += 1
        report = VisionReport(seq=self._seq, captured_at=time.time())
        try:
            jpeg, report.luminance = self.camera.capture()
        except Exception as exc:  # noqa: BLE001
            self.camera.release()
            self.status = "error"
            report.error = str(exc)
            self._publish(report, None)
            return
        try:
            text = self.client.describe_image(
                VISION_SYSTEM,
                VISION_PROMPT.format(luminance=report.luminance),
                jpeg,
                model=self.model,
            )
            for key, value in _parse_report(text).items():
                setattr(report, key, value)
        except Exception as exc:  # noqa: BLE001
            report.error = f"Scene analysis failed: {exc}"
        self._publish(report, jpeg)

    def _publish(self, report: VisionReport, jpeg: bytes | None) -> None:
        with self._lock:
            self._latest = report
            if jpeg is not None:
                self._frame = jpeg
        try:
            self.on_report(report)
        except Exception:  # noqa: BLE001
            log.exception("Cognitive Agent failed to accept vision report")
