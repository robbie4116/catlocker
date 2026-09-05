from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from hotkeys import KeyEvent


@dataclass(frozen=True, slots=True)
class RecordingEnvelope:
    session_id: object
    sequence: int
    event: KeyEvent


@dataclass(frozen=True, slots=True)
class RecordingStatus:
    session_id: object
    active: bool
    closed: bool
    overflowed: bool
    invalid: bool

    @property
    def healthy(self) -> bool:
        return self.active and not self.closed and not self.overflowed and not self.invalid


class RecordingChannel:
    """A bounded SPSC channel; deque operations are atomic under CPython's GIL."""

    def __init__(self, session_id: object, *, capacity: int = 256) -> None:
        if int(capacity) <= 0:
            raise ValueError("Recording channel capacity must be positive.")
        self.session_id = session_id
        self.capacity = int(capacity)
        self._payloads: deque[RecordingEnvelope] = deque()
        self._next_sequence = 0
        self._active = True
        self._closed = False
        self._overflowed = False
        self._invalid = False

    @property
    def status(self) -> RecordingStatus:
        return RecordingStatus(
            self.session_id,
            self._active,
            self._closed,
            self._overflowed,
            self._invalid,
        )

    def publish(self, event: KeyEvent) -> bool:
        if not self._active or self._closed or self._overflowed or self._invalid:
            return False
        if len(self._payloads) >= self.capacity:
            self._overflowed = True
            return False
        envelope = RecordingEnvelope(self.session_id, self._next_sequence, event)
        self._next_sequence += 1
        self._payloads.append(envelope)
        return True

    def drain(self, *, limit: int | None = None) -> tuple[RecordingEnvelope, ...]:
        if limit is None:
            limit = len(self._payloads)
        limit = max(0, int(limit))
        drained: list[RecordingEnvelope] = []
        while self._payloads and len(drained) < limit:
            drained.append(self._payloads.popleft())
        return tuple(drained)

    def invalidate(self) -> None:
        self._invalid = True
        self._active = False
        self._closed = True

    def close(self) -> None:
        self._active = False
        self._closed = True
