"""Bounded local diagnostics; safe to call from workers or the UI thread."""

from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import threading
import traceback


class Diagnostics:
    def __init__(self, path: Path, capacity: int = 500, max_bytes: int = 4 * 1024 * 1024):
        self.path = Path(path)
        self.max_bytes = max_bytes
        self._entries = deque(maxlen=capacity)
        self._lock = threading.RLock()
        self._secrets = set()
        self._sequence = 0
        self._version = 0
        self.write_error = ""

    def register_secret(self, value: str):
        if value:
            with self._lock:
                self._secrets.add(value)
                self._version += 1

    @property
    def version(self):
        with self._lock:
            return self._version

    def _text(self, value: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            for form in (secret, json.dumps(secret, ensure_ascii=False)[1:-1],
                         json.dumps(secret, ensure_ascii=True)[1:-1]):
                value = value.replace(form, "[已隐藏]")
        value = re.sub(r"(?i)\bBearer\s+[^\s\"'\\,;]+", "Bearer [已隐藏]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[已隐藏]", value)
        value = re.sub(r'(?i)("(?:api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password)"\s*:\s*)"(?:\\.|[^"\\])*"',
                       r'\1"[已隐藏]"', value)
        # Explicit marker preserves evidence that an exceptionally large value was cut.
        if len(value) > 48000:
            value = value[:36000] + "\n[内容过长，已截断]\n" + value[-12000:]
        return value

    def _safe(self, value):
        if is_dataclass(value):
            value = asdict(value)
        if isinstance(value, dict):
            return {self._text(str(key)): ("[已隐藏]" if re.search(
                r"(?i)^(api[_-]?key|authorization|access[_-]?token|refresh[_-]?token|password|cookie)$", str(key))
                else self._safe(item)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._safe(item) for item in value]
        if isinstance(value, str):
            return self._text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self._text(str(value))

    def record(self, event: str, level: str = "INFO", **details):
        # Diagnostics must never change whether a translation succeeds.
        try:
            with self._lock:
                self._sequence += 1
                entry = self._safe({"sequence": self._sequence,
                    "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "level": level, "event": event, "details": details})
                encoded = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
                if len(encoded) > self.max_bytes:
                    entry["details"] = {"truncated": True, "excerpt": self._text(
                        json.dumps(entry["details"], ensure_ascii=False))[:max(100, self.max_bytes // 8)]}
                    encoded = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
                self._entries.append(entry)
                self._version += 1
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    if self.path.exists() and self.path.stat().st_size + len(encoded) > self.max_bytes:
                        self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
                    with self.path.open("ab") as output:
                        output.write(encoded)
                    self.write_error = ""
                except OSError as error:
                    self.write_error = self._text(str(error))
        except Exception:
            pass

    def error(self, event: str, error: Exception, **details):
        self.record(event, "ERROR", error_type=type(error).__name__, message=str(error),
                    traceback="".join(traceback.format_exception(type(error), error, error.__traceback__)),
                    **details)

    def snapshot(self):
        with self._lock:
            # Detached, re-sanitized copies also protect against newly registered secrets.
            return self._safe(list(self._entries))

    def format(self, entries=None) -> str:
        entries = self.snapshot() if entries is None else entries
        return "\n\n".join(f"[{e['time']}] {e['level']} · {e['event']}\n"
                             + json.dumps(e["details"], ensure_ascii=False, indent=2) for e in entries)

    def export(self, path: Path):
        Path(path).write_text(self.format(), encoding="utf-8")
