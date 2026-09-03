import ctypes
import queue
import threading
import time

import pytest

from keyboard_hook import (
    CommandKind,
    CommandResult,
    EngineEvent,
    HC_ACTION,
    HookStopped,
    HookTimeout,
    HOOKPROC,
    KBDLLHOOKSTRUCT,
    LLKHF_INJECTED,
    KeyboardHook,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_APP_COMMAND,
    WM_QUIT,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
    event_from_message,
)
from hotkeys import parse_shortcut


class FakeWin32Api:
    thread_id = 42
    hook_handle = 1234
    call_next_return = 77

    def __init__(self, *, install_error=None, unhook_result=True, unhook_error=None):
        self.install_error = install_error
        self.unhook_result = unhook_result
        self.unhook_error = unhook_error
        self.install_calls = 0
        self.call_next_calls = []
        self.unhooked = []
        self.active_hooks = set()
        self.post_thread_message_calls = []
        self.post_quit_calls = []
        self.install_threads = []
        self.unhook_threads = []
        self.initialize_threads = []
        self.messages = queue.Queue()
        self.callback = None
        self._get_message_error = None
        self._get_message_error_marker = object()
        self.translate_calls = []
        self.dispatch_calls = []

    def get_current_thread_id(self):
        return self.thread_id

    def initialize_message_queue(self):
        self.initialize_threads.append(threading.get_ident())

    def fail_next_get_message(self, error):
        self._get_message_error = error
        self.messages.put((self._get_message_error_marker, 0, 0))

    def install_hook(self, callback):
        self.install_threads.append(threading.get_ident())
        self.install_calls += 1
        if self.install_error is not None:
            raise self.install_error
        self.callback = callback
        self.active_hooks.add(self.hook_handle)
        return self.hook_handle

    def call_next(self, hook, n_code, w_param, l_param):
        self.call_next_calls.append((n_code, w_param, l_param))
        return self.call_next_return

    def unhook(self, hook):
        self.unhook_threads.append(threading.get_ident())
        self.unhooked.append(hook)
        if self.unhook_error is not None:
            raise self.unhook_error
        if self.unhook_result:
            self.active_hooks.discard(hook)
        return self.unhook_result

    def post_thread_message(self, thread_id, message, w_param=0, l_param=0):
        self.post_thread_message_calls.append(
            (thread_id, message, w_param, l_param)
        )
        self.messages.put((message, w_param, l_param))
        return True

    def post_quit(self, exit_code=0):
        self.post_quit_calls.append(exit_code)
        self.messages.put((WM_QUIT, exit_code, 0))

    def get_message(self, message=None):
        item = self.messages.get()
        if item[0] is self._get_message_error_marker:
            error = self._get_message_error
            self._get_message_error = None
            raise error
        if message is None:
            return 0 if item[0] == WM_QUIT else item
        message.message, message.wParam, message.lParam = item
        return 0 if item[0] == WM_QUIT else 1

    def translate(self, message):
        self.translate_calls.append(message.message)
        return True

    def dispatch(self, message):
        self.dispatch_calls.append(message.message)
        return 0

    def emit(self, n_code, message, structure):
        return self.callback(n_code, message, ctypes.addressof(structure))


class BlockingGetMessageApi(FakeWin32Api):
    def __init__(self):
        super().__init__()
        self.release_get_message = threading.Event()

    def get_message(self, message=None):
        self.release_get_message.wait()
        return super().get_message(message)


class BlockingInstallApi(FakeWin32Api):
    def __init__(self):
        super().__init__()
        self.install_started = threading.Event()
        self.release_install = threading.Event()
        self.install_finished = threading.Event()

    def install_hook(self, callback):
        self.install_started.set()
        if not self.release_install.wait(timeout=1):
            raise AssertionError("test did not release hook installation")
        try:
            return super().install_hook(callback)
        finally:
            self.install_finished.set()


class BlockingUnhookApi(FakeWin32Api):
    def __init__(self):
        super().__init__()
        self.unhook_started = threading.Event()
        self.release_unhook = threading.Event()
        self.second_command_posted = threading.Event()

    def unhook(self, hook):
        self.unhook_started.set()
        if not self.release_unhook.wait(timeout=1):
            raise AssertionError("test did not release hook uninstallation")
        return super().unhook(hook)

    def post_thread_message(self, thread_id, message, w_param=0, l_param=0):
        posted = super().post_thread_message(thread_id, message, w_param, l_param)
        if message == WM_APP_COMMAND and len(self.post_thread_message_calls) >= 2:
            self.second_command_posted.set()
        return posted


class CancellationProbeHook(KeyboardHook):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cancellation_finished = threading.Event()

    def _cancel_command(self, command):
        super()._cancel_command(command)
        self.cancellation_finished.set()


class PausingDispatchHook(KeyboardHook):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.dispatch_started = threading.Event()
        self.release_dispatch = threading.Event()
        self.dispatch_finished = threading.Event()

    def _dispatch_command(self, command):
        if command.kind is CommandKind.SET_LOCKED:
            self.dispatch_started.set()
            if not self.release_dispatch.wait(timeout=1):
                raise AssertionError("test did not release command dispatch")
        try:
            return super()._dispatch_command(command)
        finally:
            if command.kind is CommandKind.SET_LOCKED:
                self.dispatch_finished.set()


class PausingBeforeCommitHook(KeyboardHook):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.before_commit_started = threading.Event()
        self.release_before_commit = threading.Event()
        self.command_finished = threading.Event()

    def _run_command(self, command):
        if command.kind is CommandKind.SET_LOCKED:
            self.before_commit_started.set()
            if not self.release_before_commit.wait(timeout=1):
                raise AssertionError("test did not release command before commit")
        try:
            return super()._run_command(command)
        finally:
            if command.kind is CommandKind.SET_LOCKED:
                self.command_finished.set()


class RecordingEvents:
    def __init__(self, marker):
        self.marker = marker
        self.events = queue.SimpleQueue()
        self.records = []

    def put(self, event):
        self.records.append((self.marker.is_set(), event))
        self.events.put(event)

    def get_nowait(self):
        return self.events.get_nowait()


class RaisingEvents:
    def put(self, event):
        raise RuntimeError("event publication failed")


@pytest.mark.parametrize(
    ("message", "is_keydown"),
    [
        (WM_KEYDOWN, True),
        (WM_SYSKEYDOWN, True),
        (WM_KEYUP, False),
        (WM_SYSKEYUP, False),
    ],
)
def test_keyboard_messages_translate_to_pure_events(message, is_keydown):
    data = KBDLLHOOKSTRUCT(vkCode=0x87, scanCode=0, flags=LLKHF_INJECTED, time=0, dwExtraInfo=0)
    event = event_from_message(message, data)
    assert event.vk == 0x87
    assert event.is_keydown is is_keydown
    assert event.injected is True


def test_unknown_message_is_not_a_keyboard_event():
    assert event_from_message(0x9999, KBDLLHOOKSTRUCT()) is None


def test_kbd_structure_uses_pointer_sized_extra_info():
    field_type = dict(KBDLLHOOKSTRUCT._fields_)["dwExtraInfo"]
    assert ctypes.sizeof(field_type) == ctypes.sizeof(ctypes.c_void_p)


def test_hook_callback_uses_pointer_sized_windows_types():
    assert ctypes.sizeof(HOOKPROC._argtypes_[1]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HOOKPROC._argtypes_[2]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(HOOKPROC._restype_) == ctypes.sizeof(ctypes.c_void_p)


def test_hook_installs_on_own_thread_and_unhooks_on_stop():
    caller_ident = threading.get_ident()
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    assert hook.thread.daemon is True
    assert api.install_calls == 1
    assert hook.thread_id == 42
    hook.stop(timeout=1)
    assert api.unhooked == [1234]
    assert api.install_threads == api.unhook_threads
    assert api.install_threads[0] != caller_ident
    assert not hook.is_alive()


def test_callback_suppresses_locked_key_and_passes_unlocked_key():
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    assert (
        api.emit(
            HC_ACTION,
            WM_KEYDOWN,
            KBDLLHOOKSTRUCT(vkCode=0x41),
        )
        == api.call_next_return
    )
    api.emit(HC_ACTION, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x87))
    api.emit(HC_ACTION, WM_KEYUP, KBDLLHOOKSTRUCT(vkCode=0x87))
    assert api.emit(HC_ACTION, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x42)) == 1
    hook.stop(timeout=1)


def test_negative_hook_code_always_calls_next():
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    api.emit(-1, WM_KEYDOWN, KBDLLHOOKSTRUCT(vkCode=0x87))
    assert api.call_next_calls[-1][0] == -1
    hook.stop(timeout=1)


def test_installation_exception_is_reported_without_start_timeout():
    api = FakeWin32Api(install_error=OSError("install failed"))
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    with pytest.raises(OSError, match="install failed"):
        hook.start(timeout=1)
    assert hook.ready.is_set()


def test_start_timeout_cancels_delayed_install_before_worker_can_suppress_input():
    api = BlockingInstallApi()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    start_errors = []

    def start_hook():
        try:
            hook.start(timeout=0.01)
        except BaseException as exc:
            start_errors.append(exc)

    starter = threading.Thread(target=start_hook)
    starter.start()
    try:
        assert api.install_started.wait(timeout=1)
        starter.join(timeout=1)
        assert not starter.is_alive()
        assert len(start_errors) == 1
        assert isinstance(start_errors[0], HookTimeout)
        assert hook.fail_open.is_set()

        api.release_install.set()
        assert api.install_finished.wait(timeout=1)
        api.messages.put((WM_QUIT, 0, 0))
        hook.thread.join(timeout=1)

        assert not hook.is_alive()
        assert hook.hook_handle is None
        assert api.active_hooks == set()
        assert api.install_threads == api.unhook_threads
        assert api.emit(
            HC_ACTION,
            WM_KEYDOWN,
            KBDLLHOOKSTRUCT(vkCode=0x87),
        ) == api.call_next_return
    finally:
        api.release_install.set()
        if hook.thread is not None:
            api.messages.put((WM_QUIT, 0, 0))
            hook.thread.join(timeout=1)


@pytest.mark.parametrize("timeout", [None, float("inf"), float("nan")])
def test_start_rejects_unbounded_or_nonfinite_timeout(timeout):
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    try:
        with pytest.raises(ValueError, match="finite"):
            hook.start(timeout=timeout)
    finally:
        if hook.is_alive():
            hook.stop(timeout=1)


@pytest.mark.parametrize("timeout", [None, float("inf"), float("nan")])
def test_submit_rejects_unbounded_or_nonfinite_timeout(timeout):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        with pytest.raises(ValueError, match="finite"):
            hook.submit(CommandKind.SET_LOCKED, True, timeout=timeout)
    finally:
        hook.stop(timeout=1)


@pytest.mark.parametrize("timeout", [None, float("inf"), float("nan")])
def test_stop_rejects_unbounded_or_nonfinite_timeout(timeout):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        with pytest.raises(ValueError, match="finite"):
            hook.stop(timeout=timeout)
    finally:
        hook.stop(timeout=1)


@pytest.mark.parametrize("timeout", [-1, 1e308])
def test_start_rejects_unsafe_timeout_before_worker_or_hook_side_effect(timeout):
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)

    with pytest.raises(ValueError, match="finite"):
        hook.start(timeout=timeout)

    assert hook.thread is None
    assert api.install_calls == 0
    assert api.active_hooks == set()
    assert not hook.fail_open.is_set()


@pytest.mark.parametrize("timeout", [-1, 1e308])
def test_submit_rejects_unsafe_timeout_before_posting(timeout):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        with pytest.raises(ValueError, match="finite"):
            hook.submit(CommandKind.SET_LOCKED, True, timeout=timeout)
        assert api.post_thread_message_calls == []
        assert hook.state.locked is False
    finally:
        hook.stop(timeout=1)


@pytest.mark.parametrize("timeout", [-1, 1e308])
def test_stop_rejects_unsafe_timeout_before_stopping(timeout):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        with pytest.raises(ValueError, match="finite"):
            hook.stop(timeout=timeout)
        assert hook.is_alive()
        assert api.post_thread_message_calls == []
    finally:
        hook.stop(timeout=1)


def test_stop_timeout_is_bounded_and_thread_is_daemon():
    api = BlockingGetMessageApi()
    hook = started_hook(api, "F24")
    started = time.monotonic()
    try:
        assert hook.thread.daemon is True
        with pytest.raises(HookTimeout):
            hook.stop(timeout=0.01)
        assert time.monotonic() - started < 0.5
    finally:
        api.release_get_message.set()
        api.messages.put((WM_QUIT, 0, 0))
        hook.thread.join(timeout=1)


def test_stop_does_not_hold_pending_lock_during_blocking_unhook():
    api = BlockingUnhookApi()
    hook = CancellationProbeHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    stop_errors = []

    def stop_hook():
        try:
            hook.stop(timeout=1)
        except BaseException as exc:
            stop_errors.append(exc)

    stopper = threading.Thread(target=stop_hook)
    stopper.start()
    submitter = None
    try:
        assert api.unhook_started.wait(timeout=1)

        submit_errors = []

        def submit_stop():
            try:
                hook.submit(CommandKind.STOP, timeout=0.01)
            except BaseException as exc:
                submit_errors.append(exc)

        submitter = threading.Thread(target=submit_stop)
        submitter.start()
        assert api.second_command_posted.wait(timeout=1)
        assert hook.cancellation_finished.wait(timeout=0.5)
        submitter.join(timeout=1)
        assert not submitter.is_alive()
        assert len(submit_errors) == 1
        assert isinstance(submit_errors[0], HookTimeout)
    finally:
        api.release_unhook.set()
        stopper.join(timeout=1)
        if submitter is not None:
            submitter.join(timeout=1)
        if hook.thread is not None:
            hook.thread.join(timeout=1)

    assert not stopper.is_alive()
    assert submitter is None or not submitter.is_alive()
    assert not hook.is_alive()
    assert stop_errors == []


def started_hook(api, shortcut):
    hook = KeyboardHook(parse_shortcut(shortcut), api=api)
    hook.start(timeout=1)
    return hook


def test_commands_are_serialized_on_hook_thread():
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        assert hook.submit(CommandKind.SET_LOCKED, True, timeout=1).locked is True
        assert hook.submit(CommandKind.SET_LOCKED, False, timeout=1).locked is False
        assert hook.submit(CommandKind.ENTER_RECORDING, timeout=1).accepted is True
        assert (
            hook.submit(
                CommandKind.REPLACE_SHORTCUT,
                parse_shortcut("K"),
                timeout=1,
            ).accepted
            is True
        )
        assert hook.state.recording is False
    finally:
        hook.stop(timeout=1)


def test_callback_exception_poisoning_passes_all_later_events(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        monkeypatch.setattr(
            hook.state,
            "handle",
            lambda event: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        assert (
            api.emit(
                HC_ACTION,
                WM_KEYDOWN,
                KBDLLHOOKSTRUCT(vkCode=0x41),
            )
            == api.call_next_return
        )
        before = len(api.call_next_calls)
        assert (
            api.emit(
                HC_ACTION,
                WM_KEYDOWN,
                KBDLLHOOKSTRUCT(vkCode=0x42),
            )
            == api.call_next_return
        )
        assert len(api.call_next_calls) == before + 1
        assert hook.fail_open.is_set()
        assert hook.events.get_nowait().kind == "fatal"
    finally:
        hook.stop(timeout=1)


def test_callback_state_failure_never_escapes(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    structure = KBDLLHOOKSTRUCT(vkCode=0x41)
    try:
        monkeypatch.setattr(
            hook.state,
            "handle",
            lambda event: (_ for _ in ()).throw(RuntimeError("state failed")),
        )
        monkeypatch.setattr(
            hook.state,
            "set_locked",
            lambda locked: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
        )

        assert hook._callback(
            HC_ACTION,
            WM_KEYDOWN,
            ctypes.addressof(structure),
        ) == api.call_next_return
        assert hook.fail_open.is_set()
    finally:
        hook.stop(timeout=1)


def test_callback_failure_survives_event_and_call_next_failures(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    structure = KBDLLHOOKSTRUCT(vkCode=0x41)
    try:
        monkeypatch.setattr(
            hook.state,
            "handle",
            lambda event: (_ for _ in ()).throw(RuntimeError("state failed")),
        )
        hook.events = RaisingEvents()
        monkeypatch.setattr(
            api,
            "call_next",
            lambda *args: (_ for _ in ()).throw(RuntimeError("next failed")),
        )

        assert hook._callback(
            HC_ACTION,
            WM_KEYDOWN,
            ctypes.addressof(structure),
        ) == 0
        assert hook.fail_open.is_set()
    finally:
        hook.stop(timeout=1)


def test_callback_initial_call_next_failure_returns_fail_open_result(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    structure = KBDLLHOOKSTRUCT(vkCode=0x41)
    try:
        monkeypatch.setattr(
            api,
            "call_next",
            lambda *args: (_ for _ in ()).throw(RuntimeError("next failed")),
        )

        assert hook._callback(
            -1,
            WM_KEYDOWN,
            ctypes.addressof(structure),
        ) == 0
        assert hook.fail_open.is_set()
    finally:
        hook.stop(timeout=1)


def test_callback_failure_does_not_wait_for_state_lock(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    callback_result = []
    try:
        monkeypatch.setattr(
            api,
            "call_next",
            lambda *args: (_ for _ in ()).throw(RuntimeError("next failed")),
        )
        assert hook._state_lock.acquire(blocking=False)
        callback_thread = threading.Thread(
            target=lambda: callback_result.append(
                hook._callback(-1, WM_KEYDOWN, 0)
            )
        )
        callback_thread.start()
        assert callback_thread.join(timeout=0.2) is None
        assert not callback_thread.is_alive()
        assert callback_result == [0]
        assert not hook.fail_open.is_set()
        assert hook._fail_open_pending.is_set()
        hook._state_lock.release()
        assert hook.submit(CommandKind.FAIL_OPEN, timeout=1).accepted is True
        assert hook.fail_open.is_set()
    finally:
        if hook._state_lock.locked():
            hook._state_lock.release()
        hook.stop(timeout=1)


def test_fail_open_serializes_paused_command_state_commit(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    state_commit_started = threading.Event()
    release_state_commit = threading.Event()
    fail_open_invoked = threading.Event()
    fail_open_finished = threading.Event()
    hook.events = RecordingEvents(fail_open_finished)
    original_set_locked = hook.state.set_locked

    def paused_set_locked(locked):
        if locked:
            state_commit_started.set()
            if not release_state_commit.wait(timeout=1):
                raise AssertionError("test did not release state commit")
        return original_set_locked(locked)

    monkeypatch.setattr(hook.state, "set_locked", paused_set_locked)
    command_result = []
    fail_open_thread = None
    try:
        command_thread = threading.Thread(
            target=lambda: command_result.append(
                hook.submit(CommandKind.SET_LOCKED, True, timeout=1)
            )
        )
        command_thread.start()
        assert state_commit_started.wait(timeout=1)

        def enter_fail_open():
            fail_open_invoked.set()
            hook.enter_fail_open()
            fail_open_finished.set()

        fail_open_thread = threading.Thread(target=enter_fail_open)
        fail_open_thread.start()
        assert fail_open_invoked.wait(timeout=1)
        release_state_commit.set()
        command_thread.join(timeout=1)
        fail_open_thread.join(timeout=1)

        assert not command_thread.is_alive()
        assert not fail_open_thread.is_alive()
        assert len(command_result) == 1
        hook.submit(CommandKind.FAIL_OPEN, timeout=1)
        assert hook.state.locked is False
        assert not any(
            marked and event.kind == "state" and event.locked
            for marked, event in hook.events.records
        )
    finally:
        release_state_commit.set()
        if fail_open_thread is not None:
            fail_open_thread.join(timeout=1)
        hook.stop(timeout=1)


def test_fail_open_serializes_paused_callback_state_transition(monkeypatch):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    state_transition_started = threading.Event()
    release_state_transition = threading.Event()
    fail_open_invoked = threading.Event()
    fail_open_finished = threading.Event()
    hook.events = RecordingEvents(fail_open_finished)
    original_handle = hook.state.handle

    def paused_handle(event):
        state_transition_started.set()
        if not release_state_transition.wait(timeout=1):
            raise AssertionError("test did not release callback transition")
        return original_handle(event)

    monkeypatch.setattr(hook.state, "handle", paused_handle)
    callback_results = []
    fail_open_thread = None
    try:
        callback_thread = threading.Thread(
            target=lambda: callback_results.append(
                api.emit(
                    HC_ACTION,
                    WM_KEYDOWN,
                    KBDLLHOOKSTRUCT(vkCode=0x87),
                )
            )
        )
        callback_thread.start()
        assert state_transition_started.wait(timeout=1)

        def enter_fail_open():
            fail_open_invoked.set()
            hook.enter_fail_open()
            fail_open_finished.set()

        fail_open_thread = threading.Thread(target=enter_fail_open)
        fail_open_thread.start()
        assert fail_open_invoked.wait(timeout=1)
        release_state_transition.set()
        callback_thread.join(timeout=1)
        fail_open_thread.join(timeout=1)

        assert not callback_thread.is_alive()
        assert not fail_open_thread.is_alive()
        assert callback_results == [1]
        hook.submit(CommandKind.FAIL_OPEN, timeout=1)
        assert hook.state.locked is False
        assert not any(
            marked and event.kind == "state" and event.locked
            for marked, event in hook.events.records
        )
    finally:
        release_state_transition.set()
        if fail_open_thread is not None:
            fail_open_thread.join(timeout=1)
        hook.stop(timeout=1)


def test_fail_open_irreversibly_rejects_later_state_changes():
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        hook.enter_fail_open()
        with pytest.raises(HookStopped):
            hook.submit(CommandKind.SET_LOCKED, True, timeout=1)
        with pytest.raises(HookStopped):
            hook.submit(
                CommandKind.REPLACE_SHORTCUT,
                parse_shortcut("K"),
                timeout=1,
            )
        assert hook.locked is False
    finally:
        hook.stop(timeout=1)


def test_message_loop_error_enters_fail_open_and_unhooks_on_owner_thread():
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    api.fail_next_get_message(OSError("GetMessageW failed"))
    hook.thread.join(timeout=1)
    assert not hook.is_alive()
    assert hook.fail_open.is_set()
    event = hook.events.get_nowait()
    assert event.kind == "fatal"
    assert event.reason == "message_loop"
    assert isinstance(event.error, OSError)
    assert api.unhooked == [1234]
    assert api.install_threads == api.unhook_threads


def test_recording_is_rejected_while_locked():
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        assert hook.submit(CommandKind.SET_LOCKED, True, timeout=1).accepted is True
        result = hook.submit(CommandKind.ENTER_RECORDING, timeout=1)
        assert result.accepted is False
        assert hook.state.recording is False
    finally:
        hook.stop(timeout=1)


@pytest.mark.parametrize("kind", [CommandKind.SET_LOCKED, CommandKind.TOGGLE])
def test_external_lock_or_toggle_exits_recording(kind):
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        assert hook.submit(CommandKind.ENTER_RECORDING, timeout=1).accepted is True
        assert hook.state.recording is True
        if kind is CommandKind.SET_LOCKED:
            result = hook.submit(kind, True, timeout=1)
        else:
            result = hook.submit(kind, timeout=1)
        assert result.accepted is True
        assert result.locked is True
        assert hook.state.recording is False
    finally:
        hook.stop(timeout=1)


def test_shortcut_generation_increments_only_for_accepted_replacement():
    api = FakeWin32Api()
    hook = started_hook(api, "F24")
    try:
        assert hook.shortcut_generation == 0
        assert (
            hook.submit(
                CommandKind.REPLACE_SHORTCUT,
                parse_shortcut("K"),
                timeout=1,
            ).accepted
            is True
        )
        assert hook.shortcut_generation == 1
        hook.submit(CommandKind.SET_LOCKED, True, timeout=1)
        assert (
            hook.submit(
                CommandKind.REPLACE_SHORTCUT,
                parse_shortcut("F24"),
                timeout=1,
            ).accepted
            is False
        )
        assert hook.shortcut_generation == 1
    finally:
        hook.stop(timeout=1)


def test_timed_out_popped_state_changing_command_is_canceled_before_mutation():
    api = FakeWin32Api()
    hook = PausingDispatchHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    submit_errors = []

    def submit_lock_command():
        try:
            hook.submit(CommandKind.SET_LOCKED, True, timeout=0.01)
        except BaseException as exc:
            submit_errors.append(exc)

    submitter = threading.Thread(target=submit_lock_command)
    submitter.start()
    try:
        assert hook.dispatch_started.wait(timeout=1)
        submitter.join(timeout=1)
        assert not submitter.is_alive()
        assert len(submit_errors) == 1
        assert isinstance(submit_errors[0], HookTimeout)

        hook.release_dispatch.set()
        assert hook.dispatch_finished.wait(timeout=1)
        assert hook.state.locked is False
        assert hook.locked is False
    finally:
        hook.release_dispatch.set()
        hook.stop(timeout=1)


def test_timed_out_claimed_state_command_is_canceled_before_commit():
    api = FakeWin32Api()
    hook = PausingBeforeCommitHook(parse_shortcut("F24"), api=api)
    hook.start(timeout=1)
    submit_errors = []
    submitter_done = threading.Event()
    submitter = None

    try:
        assert hook.submit(CommandKind.ENTER_RECORDING, timeout=1).accepted is True
        initial_state = (
            hook.state.locked,
            hook.state.recording,
            hook.shortcut_generation,
        )

        def submit_lock_command():
            try:
                hook.submit(CommandKind.SET_LOCKED, True, timeout=0.01)
            except BaseException as exc:
                submit_errors.append(exc)
            finally:
                submitter_done.set()

        submitter = threading.Thread(target=submit_lock_command)
        submitter.start()
        assert hook.before_commit_started.wait(timeout=1)
        assert submitter_done.wait(timeout=0.25), (
            "timed-out submitter blocked while the command was paused before commit"
        )
        assert len(submit_errors) == 1
        assert isinstance(submit_errors[0], HookTimeout)

        hook.release_before_commit.set()
        assert hook.command_finished.wait(timeout=1)
        assert (
            hook.state.locked,
            hook.state.recording,
            hook.shortcut_generation,
        ) == initial_state
    finally:
        hook.release_before_commit.set()
        if submitter is not None:
            submitter.join(timeout=1)
        hook.stop(timeout=1)


@pytest.mark.parametrize("failure", ["return_false", "raise"])
def test_unhook_failure_retains_handle_and_stop_reports_cleanup_error(failure):
    if failure == "return_false":
        api = FakeWin32Api(unhook_result=False)
    else:
        api = FakeWin32Api(unhook_error=OSError("unhook failed"))
    hook = started_hook(api, "F24")

    with pytest.raises(HookStopped, match="unhook"):
        hook.stop(timeout=1)

    assert not hook.is_alive()
    assert hook.fail_open.is_set()
    assert hook.hook_handle == 1234
    assert api.active_hooks == {1234}
    cleanup_events = []
    while True:
        try:
            cleanup_events.append(hook.events.get_nowait())
        except queue.Empty:
            break
    assert any(
        event.kind == "fatal" and event.reason == "cleanup"
        for event in cleanup_events
    )


def test_emergency_force_unhook_is_idempotent_and_fail_open():
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.hook_handle = api.hook_handle

    hook.force_unhook()
    hook.force_unhook()

    assert api.unhooked == [api.hook_handle]
    assert hook.hook_handle is None
    assert hook.locked is False


def test_emergency_post_quit_is_idempotent():
    api = FakeWin32Api()
    hook = KeyboardHook(parse_shortcut("F24"), api=api)
    hook.thread_id = api.thread_id

    hook.post_quit()
    hook.post_quit()

    assert [call[1] for call in api.post_thread_message_calls] == [WM_QUIT]


class ReplyNoiseApi(FakeWin32Api):
    def __init__(self):
        super().__init__()
        self.hook = None

    def post_thread_message(self, thread_id, message, w_param=0, l_param=0):
        self.post_thread_message_calls.append(
            (thread_id, message, w_param, l_param)
        )
        if message == WM_APP_COMMAND:
            command = self.hook._pending_commands[w_param]
            command.reply.put(
                CommandResult(
                    w_param + 1000,
                    False,
                    False,
                    command.command_id,
                )
            )
        self.messages.put((message, w_param, l_param))
        return True


class LateAckApi(FakeWin32Api):
    def __init__(self):
        super().__init__()
        self.hook = None
        self.late_command_id = None
        self.late_reply = None

    def post_thread_message(self, thread_id, message, w_param=0, l_param=0):
        self.post_thread_message_calls.append(
            (thread_id, message, w_param, l_param)
        )
        if message == WM_APP_COMMAND and self.late_command_id is None:
            self.late_command_id = w_param
            self.late_reply = self.hook._pending_commands[w_param].reply
            return True
        self.messages.put((message, w_param, l_param))
        return True

    def release_late_acknowledgement(self):
        self.late_reply.put(
            CommandResult(self.late_command_id, True, True, 0)
        )
        self.messages.put((WM_APP_COMMAND, self.late_command_id, 0))


def test_mismatched_command_ids_are_ignored_until_matching_result():
    api = ReplyNoiseApi()
    hook = started_hook(api, "F24")
    api.hook = hook
    try:
        result = hook.submit(CommandKind.SET_LOCKED, True, timeout=1)
        assert result.command_id == 1
        assert result.accepted is True
        assert result.locked is True
    finally:
        hook.stop(timeout=1)


def test_late_acknowledgement_for_timed_out_command_is_ignored():
    api = LateAckApi()
    hook = started_hook(api, "F24")
    api.hook = hook
    try:
        with pytest.raises(HookTimeout):
            hook.submit(CommandKind.SET_LOCKED, True, timeout=0.01)
        assert hook._pending_commands == {}
        api.release_late_acknowledgement()
        result = hook.submit(CommandKind.SET_LOCKED, False, timeout=1)
        assert result.accepted is True
        assert result.locked is False
    finally:
        hook.stop(timeout=1)
