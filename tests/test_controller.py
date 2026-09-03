from __future__ import annotations

import queue
from dataclasses import dataclass

import pytest

from hotkeys import parse_shortcut
from keyboard_hook import (
    CommandKind,
    CommandResult,
    EngineEvent,
    HookStopped,
    HookTimeout,
)
from controller import CatModeController, EngineUnhealthy


@dataclass(frozen=True, slots=True)
class HookCall:
    kind: CommandKind
    payload: object
    timeout: float


class FakeHook:
    def __init__(
        self,
        *,
        locked: bool = False,
        result: CommandResult | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.locked = locked
        self.events: queue.SimpleQueue[EngineEvent] = queue.SimpleQueue()
        self.calls: list[HookCall] = []
        self.fail_open_calls = 0
        self.result = result or CommandResult(1, True, locked, 0)
        self.error = error

    def submit(
        self,
        kind: CommandKind,
        payload: object = None,
        *,
        timeout: float = 1.0,
    ) -> CommandResult:
        self.calls.append(HookCall(kind, payload, timeout))
        if self.error is not None:
            raise self.error
        return self.result

    def enter_fail_open(self) -> None:
        self.fail_open_calls += 1
        self.locked = False


def test_controller_routes_all_lock_actions_to_same_engine():
    hook = FakeHook()
    controller = CatModeController(hook, command_timeout=0.25)

    controller.lock()
    controller.unlock()
    controller.toggle()

    assert [call.kind for call in hook.calls] == [
        CommandKind.SET_LOCKED,
        CommandKind.SET_LOCKED,
        CommandKind.TOGGLE,
    ]
    assert [call.timeout for call in hook.calls] == [0.25, 0.25, 0.25]
    assert hook.calls[0].payload is True
    assert hook.calls[1].payload is False
    assert hook.calls[2].payload is None


def test_controller_exposes_authoritative_snapshot_and_event_queue():
    hook = FakeHook()
    controller = CatModeController(hook)

    assert controller.locked is False
    hook.locked = True
    assert controller.locked is True
    assert controller.events is hook.events

    event = EngineEvent("state", True, "manual")
    hook.events.put(event)
    assert controller.events.get_nowait() is event


def test_replace_shortcut_returns_none_when_hook_rejects_it():
    hook = FakeHook(result=CommandResult(1, False, True, 4))
    controller = CatModeController(hook, command_timeout=0.25)
    shortcut = parse_shortcut("K")

    assert controller.replace_shortcut(shortcut) is None
    assert hook.calls == [HookCall(CommandKind.REPLACE_SHORTCUT, shortcut, 0.25)]


def test_accepted_replacement_returns_new_generation():
    hook = FakeHook(result=CommandResult(1, True, False, 5))
    controller = CatModeController(hook)

    assert controller.replace_shortcut(parse_shortcut("K")) == 5


@pytest.mark.parametrize(
    ("method_name", "kind", "payload"),
    [
        ("replace_shortcut", CommandKind.REPLACE_SHORTCUT, parse_shortcut("K")),
        ("enter_recording", CommandKind.ENTER_RECORDING, None),
        ("exit_recording", CommandKind.EXIT_RECORDING, None),
    ],
)
def test_non_lock_commands_return_hook_acknowledgements(
    method_name: str,
    kind: CommandKind,
    payload: object,
):
    hook = FakeHook(result=CommandResult(1, True, False, 7))
    controller = CatModeController(hook, command_timeout=0.25)

    if payload is None:
        result = getattr(controller, method_name)()
    else:
        result = getattr(controller, method_name)(payload)

    assert result == (7 if kind is CommandKind.REPLACE_SHORTCUT else True)
    assert hook.calls == [HookCall(kind, payload, 0.25)]


@pytest.mark.parametrize(
    ("method_name", "kind", "payload"),
    [
        ("replace_shortcut", CommandKind.REPLACE_SHORTCUT, parse_shortcut("K")),
        ("enter_recording", CommandKind.ENTER_RECORDING, None),
        ("exit_recording", CommandKind.EXIT_RECORDING, None),
    ],
)
def test_non_lock_command_timeout_enters_fail_open_and_raises(
    method_name: str,
    kind: CommandKind,
    payload: object,
):
    hook = FakeHook(error=HookTimeout("stalled"))
    controller = CatModeController(hook, command_timeout=0.25)

    with pytest.raises(EngineUnhealthy):
        if payload is None:
            getattr(controller, method_name)()
        else:
            getattr(controller, method_name)(payload)

    assert hook.fail_open_calls == 1
    assert hook.calls == [HookCall(kind, payload, 0.25)]


def test_timeout_enters_terminal_fail_open_and_raises():
    hook = FakeHook(error=HookTimeout("stalled"))
    controller = CatModeController(hook, command_timeout=0.25)

    with pytest.raises(EngineUnhealthy):
        controller.lock()

    assert hook.fail_open_calls == 1


def test_post_failure_enters_terminal_fail_open_and_raises():
    hook = FakeHook(error=HookStopped("post failed"))
    controller = CatModeController(hook, command_timeout=0.25)

    with pytest.raises(EngineUnhealthy):
        controller.toggle()

    assert hook.fail_open_calls == 1


def test_controller_exposes_explicit_terminal_fail_open():
    hook = FakeHook()
    controller = CatModeController(hook)

    controller.enter_fail_open()

    assert hook.fail_open_calls == 1
