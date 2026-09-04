from __future__ import annotations

import queue

from hotkeys import Shortcut
from keyboard_hook import (
    CommandKind,
    CommandResult,
    EngineEvent,
    HookStopped,
    HookTimeout,
    KeyboardHook,
)


class EngineUnhealthy(RuntimeError):
    """Raised when the keyboard hook can no longer acknowledge a command."""


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

    def exit_recording(self) -> bool:
        return self._submit(CommandKind.EXIT_RECORDING).accepted

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
