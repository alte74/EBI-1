from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from sentient_ai.config import ROOT

log = logging.getLogger(__name__)

MEMORY_DIR = ROOT / "memory"
MEMORY_KINDS = ("chat", "vision", "note")
CHAT_ROLES = ("user", "bot", "interrupt")
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
MAX_IMAGE_BYTES = 8 * 1024 * 1024


def decode_image(data_url: str) -> tuple[bytes, str]:
    """Return (bytes, file suffix) for a base64 `data:image/...` URL."""
    header, sep, payload = data_url.partition(",")
    if not sep or not header.startswith("data:") or ";base64" not in header:
        raise ValueError("Pictures must be sent as a base64 data URL.")
    media = header[5:].split(";")[0].strip().lower()
    suffix = IMAGE_TYPES.get(media)
    if suffix is None:
        raise ValueError(f"Unsupported picture type: {media or 'unknown'}.")
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Picture data is not valid base64.") from exc
    if not data:
        raise ValueError("Picture is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Picture is larger than 8 MB.")
    return data, suffix


class MemoryStore:
    """Persistent memories: chat lines, Vision Agent snapshots, and user-added notes."""

    def __init__(self, root: Path = MEMORY_DIR) -> None:
        self.root = root
        self.images = root / "images"
        self.index = root / "memories.json"
        self.lock = threading.Lock()
        # Milliseconds, not ns: the browser parses JSON numbers as doubles (exact only below 2**53).
        self.version = int(time.time() * 1000)
        self.items: list[dict] = self._load()

    def _load(self) -> list[dict]:
        if not self.index.exists():
            return []
        try:
            data = json.loads(self.index.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = self.index.with_name(f"memories.corrupt-{int(time.time())}.json")
            log.exception("Memory index unreadable; moved it to %s", backup.name)
            self.index.replace(backup)
            return []
        return [item for item in data if isinstance(item, dict) and item.get("id")]

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.index.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.index)
        self.version += 1

    def _find(self, memory_id: str) -> dict:
        for item in self.items:
            if item["id"] == memory_id:
                return item
        raise KeyError(memory_id)

    def _write_image(self, item: dict, data: bytes, suffix: str) -> None:
        self.images.mkdir(parents=True, exist_ok=True)
        name = f"{item['id']}{suffix}"
        (self.images / name).write_bytes(data)
        if item.get("image") and item["image"] != name:
            (self.images / item["image"]).unlink(missing_ok=True)
        item["image"] = name

    def _drop_image(self, item: dict) -> None:
        if item.get("image"):
            (self.images / item["image"]).unlink(missing_ok=True)
        item["image"] = None

    def snapshot(self) -> dict:
        with self.lock:
            return {"version": self.version, "items": [dict(item) for item in self.items]}

    def image_path(self, memory_id: str) -> Path | None:
        with self.lock:
            try:
                name = self._find(memory_id).get("image")
            except KeyError:
                return None
        if not name:
            return None
        path = self.images / name
        return path if path.exists() else None

    def add(
        self,
        kind: str,
        text: str,
        *,
        role: str | None = None,
        image: bytes | None = None,
        suffix: str = ".jpg",
        meta: dict | None = None,
    ) -> dict:
        if kind not in MEMORY_KINDS:
            raise ValueError(f"Unknown memory kind: {kind}")
        if kind == "chat" and role not in CHAT_ROLES:
            raise ValueError(f"Unknown chat role: {role}")
        text = (text or "").strip()
        if not text and image is None:
            raise ValueError("A memory needs text or a picture.")
        item = {
            "id": uuid.uuid4().hex,
            "kind": kind,
            "role": role if kind == "chat" else None,
            "text": text,
            "image": None,
            "meta": meta or {},
            "created_at": time.time(),
            "updated_at": None,
        }
        with self.lock:
            if image is not None:
                self._write_image(item, image, suffix)
            self.items.append(item)
            self._save()
            return dict(item)

    def add_note(self, text: str, image_data_url: str | None = None) -> dict:
        image, suffix = decode_image(image_data_url) if image_data_url else (None, ".jpg")
        return self.add("note", text, image=image, suffix=suffix)

    def update(
        self,
        memory_id: str,
        *,
        text: str | None = None,
        image_data_url: str | None = None,
        remove_image: bool = False,
    ) -> dict:
        image = decode_image(image_data_url) if image_data_url else None
        with self.lock:
            item = self._find(memory_id)
            new_text = item["text"] if text is None else text.strip()
            keeps_image = image is not None or (item.get("image") and not remove_image)
            if not new_text and not keeps_image:
                raise ValueError("A memory needs text or a picture.")
            item["text"] = new_text
            if image is not None:
                self._write_image(item, *image)
            elif remove_image:
                self._drop_image(item)
            item["updated_at"] = time.time()
            self._save()
            return dict(item)

    def delete(self, memory_id: str) -> None:
        with self.lock:
            item = self._find(memory_id)
            self._drop_image(item)
            self.items.remove(item)
            self._save()
