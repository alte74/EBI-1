from __future__ import annotations

import asyncio
import queue
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sentient_ai.session import BotSession

WEB_DIR = Path(__file__).resolve().parent.parent / "web" / "static"
SESSION = BotSession()
app = FastAPI(title="Sentient AI")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


class SensorPayload(BaseModel):
    volume: float | None = None
    light: float | None = None
    lumens: float | None = None
    temperature: float | None = None
    humidity: float | None = None
    smell: str | None = None
    surface_touch: str | None = None


class ChatPayload(BaseModel):
    message: str = Field(min_length=1)
    sensors: SensorPayload | None = None


class EmotionBindPayload(BaseModel):
    sense: str
    band: str
    emotion: str


class ThresholdPayload(BaseModel):
    sense: str
    low: float
    high: float


def _sensor_dict(payload: SensorPayload | None) -> dict | None:
    if payload is None:
        return None
    data = payload.model_dump(exclude_none=True)
    return data or None


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/state")
def get_state() -> dict:
    return SESSION.public_state()


@app.post("/api/draft")
def set_draft(payload: SensorPayload) -> dict:
    return SESSION.set_draft(payload.model_dump(exclude_none=True))


@app.post("/api/apply")
def apply(payload: SensorPayload | None = None, defer: bool = False) -> dict:
    return SESSION.apply_draft(_sensor_dict(payload), defer_poll=defer)


@app.post("/api/preview/utterance")
def preview_utterance(payload: SensorPayload | None = None) -> dict:
    return SESSION.preview_utterances(_sensor_dict(payload))


@app.post("/api/preview/replies")
def preview_replies(payload: ChatPayload) -> dict:
    try:
        return SESSION.preview_replies(payload.message, _sensor_dict(payload.sensors))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/reset/environment")
def reset_environment() -> dict:
    return SESSION.reset_environment()


@app.post("/api/reset/conversation")
def reset_conversation() -> dict:
    return SESSION.reset_conversation()


@app.post("/api/emotion-map")
def set_emotion_binding(payload: EmotionBindPayload) -> dict:
    try:
        return SESSION.set_emotion_binding(payload.sense, payload.band, payload.emotion)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/emotion-map/reset")
def reset_emotion_map() -> dict:
    return SESSION.reset_emotion_map()


@app.post("/api/thresholds")
def set_threshold(payload: ThresholdPayload) -> dict:
    try:
        return SESSION.set_threshold(payload.sense, payload.low, payload.high)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/thresholds/reset")
def reset_thresholds() -> dict:
    return SESSION.reset_thresholds()


@app.websocket("/ws/chat")
async def chat_socket(ws: WebSocket) -> None:
    await ws.accept()
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") != "chat":
                await ws.send_json({"type": "error", "message": "Send {type:'chat', text:'...'}"})
                continue
            events: queue.Queue = queue.Queue()

            def run() -> None:
                try:
                    for event in SESSION.stream_chat(str(data.get("text") or "")):
                        events.put(event)
                except Exception as exc:  # noqa: BLE001
                    events.put({"type": "error", "message": str(exc)})
                finally:
                    events.put(None)

            threading.Thread(target=run, daemon=True).start()
            loop = asyncio.get_running_loop()
            while True:
                event = await loop.run_in_executor(None, events.get)
                if event is None:
                    break
                await ws.send_json(event)
    except WebSocketDisconnect:
        SESSION.stop_stream.set()
