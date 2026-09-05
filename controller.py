from __future__ import annotations

import queue
from dataclasses import dataclass

from hotkeys import Shortcut
from keyboard_hook import (
    CommandKind,
    CommandResult,
    EngineEvent,
    HookStopped,
    HookTimeout,
    KeyboardHook,
)
from recording_channel import RecordingChannel


class EngineUnhealthy(RuntimeError):
    """Raised when the keyboard hook can no longer acknowledge a command."""


@dataclass(frozen=True, slots=True)
class RecordingSession:
    session_id: object
    held_keys: frozenset[int]
    channel: RecordingChannel


class CatModeController:
    def __init__(
        self,
        hook: KeyboardHook,
        command_timeout: float = 1.0,
    ) -> None:
        self.hook = hook
        self.command_timeout = command_timeout

    @property
    def locked(self) -> bool:
        return self.hook.locked

    @property
    def events(self) -> queue.SimpleQueue[EngineEvent]:
        return self.hook.events

    def lock(self) -> None:
        self._submit(CommandKind.SET_LOCKED, True)

    def unlock(self, *, timeout: float | None = None) -> None:
        self._submit(CommandKind.SET_LOCKED, False, timeout=timeout)

    def toggle(self) -> None:
        self._submit(CommandKind.TOGGLE)

    def replace_shortcut(self, shortcut: Shortcut) -> int | None:
        result = self._submit(CommandKind.REPLACE_SHORTCUT, shortcut)
        return result.shortcut_generation if result.accepted else None

    def enter_recording(self) -> bool:
        return self._submit(CommandKind.ENTER_RECORDING).accepted

    def begin_recording(self) -> RecordingSession | None:
        result = self._submit(CommandKind.ENTER_RECORDING)
        if not result.accepted:
            return None
        if (
            result.recording_session_id is None
            or result.recording_channel is None
        ):
            raise EngineUnhealthy("Keyboard engine returned no recording session.")
        return RecordingSession(
            result.recording_session_id,
            result.held_keys,
            result.recording_channel,
        )

    def exit_recording(self) -> bool:
        return self._submit(CommandKind.EXIT_RECORDING).accepted

    def finish_recording(self, session_id: object) -> bool:
        return self._submit(CommandKind.FINISH_RECORDING, session_id).accepted

    def cancel_recording(self, session_id: object) -> bool:
        return self._submit(CommandKind.CANCEL_RECORDING, session_id).accepted

    def enter_fail_open(self) -> None:
        self.hook.enter_fail_open()

    def _submit(
        self,
        kind: CommandKind,
        payload: object = None,
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        try:
            return self.hook.submit(
                kind,
                payload,
                timeout=self.command_timeout if timeout is None else timeout,
            )
        except (HookTimeout, HookStopped) as exc:
            try:
                self.hook.enter_fail_open()
            finally:
                raise EngineUnhealthy(
                    f"Keyboard hook command failed: {exc}"
                ) from exc
