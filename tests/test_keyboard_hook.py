import ctypes
import queue
import threading

import pytest

from keyboard_hook import (
    CommandKind,
    CommandResult,
    EngineEvent,
    HC_ACTION,
    HookStopped,
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

    def __init__(self, *, install_error=None):
        self.install_error = install_error
        self.install_calls = 0
        self.call_next_calls = []
        self.unhooked = []
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
        return self.hook_handle

    def call_next(self, hook, n_code, w_param, l_param):
        self.call_next_calls.append((n_code, w_param, l_param))
        return self.call_next_return

    def unhook(self, hook):
        self.unhook_threads.append(threading.get_ident())
        self.unhooked.append(hook)
        return True

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
