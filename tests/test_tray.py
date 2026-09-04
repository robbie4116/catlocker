from pathlib import Path
from queue import Queue, SimpleQueue
from types import SimpleNamespace
import threading
import time

import pytest

from tray import (
    MenuCommand,
    NativeTray,
    TrayAction,
    TrayState,
    TrayUpdate,
    build_menu_state,
)


TRAY_CALLBACK_MESSAGE = 0x8003
WM_RBUTTONUP = 0x0205


def test_unlocked_menu_enables_only_valid_state_actions():
    state = build_menu_state(locked=False, startup_enabled=False)
    assert state.enabled(MenuCommand.LOCK)
    assert not state.enabled(MenuCommand.UNLOCK)
    assert state.enabled(MenuCommand.SETTINGS)
    assert not state.checked(MenuCommand.STARTUP)


def test_locked_menu_keeps_mouse_unlock_available_and_disables_settings():
    state = build_menu_state(locked=True, startup_enabled=True)
    assert not state.enabled(MenuCommand.LOCK)
    assert state.enabled(MenuCommand.UNLOCK)
    assert not state.enabled(MenuCommand.SETTINGS)
    assert state.checked(MenuCommand.STARTUP)


def test_menu_selection_enqueues_action_instead_of_mutating_state():
    actions = SimpleQueue()
    state = TrayState(actions=actions, locked=True)
    state.handle_menu_command(MenuCommand.UNLOCK)
    assert actions.get_nowait() == TrayAction.UNLOCK
    assert state.locked is True


class FakeTrayApi:
    taskbar_created_message = 0xC001

    def __init__(
        self,
        *,
        load_error=None,
        create_window_error=None,
        taskbar_error=None,
        add_error=None,
        set_version_error=None,
        notification_error=None,
        message_loop_error=None,
        delete_error=None,
    ):
        self.load_error = load_error
        self.create_window_error = create_window_error
        self.taskbar_error = taskbar_error
        self.add_error = add_error
        self.set_version_error = set_version_error
        self.notification_error = notification_error
        self.message_loop_error = message_loop_error
        self.delete_error = delete_error
        self.icon_handle = 0x1001
        self.window_handle = 0x2001
        self.window_proc = None
        self.menu_selection = 0
        self._messages = Queue()
        self._quit = threading.Event()
        self.loop_started = threading.Event()
        self.calls = []
        self.add_calls = []
        self.set_version_calls = []
        self.modify_calls = []
        self.delete_calls = []
        self.add_threads = []
        self.modify_threads = []
        self.delete_threads = []
        self.destroy_icon_calls = []
        self.destroy_window_calls = []
        self.notification_calls = []
        self.notification_attempts = 0
        self.show_menu_calls = []
        self.message_loop_calls = []
        self.post_message_threads = []
        self.taskbar_message = None

    @staticmethod
    def _tooltip(data):
        return getattr(data, "tooltip", getattr(data, "szTip", ""))

    def _record(self, name):
        self.calls.append((name, threading.get_ident()))

    def load_icon(self, icon_path):
        self._record("load_icon")
        if self.load_error is not None:
            raise self.load_error
        return self.icon_handle

    def destroy_icon(self, icon_handle):
        self._record("destroy_icon")
        self.destroy_icon_calls.append(icon_handle)

    def create_window(self, window_class):
        self._record("create_window")
        if self.create_window_error is not None:
            raise self.create_window_error
        self.window_proc = window_class.lpfnWndProc
        return self.window_handle

    def destroy_window(self, window_handle):
        self._record("destroy_window")
        self.destroy_window_calls.append(window_handle)

    def register_taskbar_created(self):
        self._record("register_taskbar_created")
        if self.taskbar_error is not None:
            raise self.taskbar_error
        self.taskbar_message = self.taskbar_created_message
        return self.taskbar_message

    def add_icon(self, data):
        self._record("add_icon")
        self.add_threads.append(threading.get_ident())
        if self.add_error is not None:
            raise self.add_error
        self.add_calls.append(SimpleNamespace(tooltip=self._tooltip(data)))
        return True

    def set_version(self, data):
        self._record("set_version")
        self.set_version_calls.append(SimpleNamespace(tooltip=self._tooltip(data)))
        if self.set_version_error is not None:
            raise self.set_version_error
        return True

    def modify_icon(self, data):
        self._record("modify_icon")
        self.modify_threads.append(threading.get_ident())
        self.modify_calls.append(SimpleNamespace(tooltip=self._tooltip(data)))
        return True

    def delete_icon(self, data):
        self._record("delete_icon")
        self.delete_threads.append(threading.get_ident())
        self.delete_calls.append(data)
        if self.delete_error is not None:
            raise self.delete_error
        return True

    def show_menu(self, state, window_handle):
        self._record("show_menu")
        self.show_menu_calls.append(state)
        return self.menu_selection

    def show_notification(self, data, title, message):
        self._record("show_notification")
        self.notification_attempts += 1
        if self.notification_error is not None:
            raise self.notification_error
        self.notification_calls.append(
            SimpleNamespace(title=title, message=message, tooltip=self._tooltip(data))
        )
        return True

    def post_message(self, window_handle, message, wparam=0, lparam=0):
        self._record("post_message")
        self.post_message_threads.append(threading.get_ident())
        self._messages.put((message, wparam, lparam))
        return True

    def quit_message(self):
        self._record("quit_message")
        self._quit.set()

    def message_loop(self, window_proc):
        self._record("message_loop")
        self.loop_started.set()
        if self.message_loop_error is not None:
            raise self.message_loop_error
        while not self._quit.is_set():
            try:
                message, wparam, lparam = self._messages.get(timeout=0.05)
            except Exception:
                continue
            self.message_loop_calls.append((message, wparam, lparam))
            window_proc(0, message, wparam, lparam)

    def emit_taskbar_created(self):
        self.post_message(self.window_handle, self.taskbar_message)

    def emit_menu_click(self):
        self.post_message(self.window_handle, TRAY_CALLBACK_MESSAGE, 0, WM_RBUTTONUP)

    def wait_until(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        assert predicate()


def started_tray(*, notifications=True, notification_error=None):
    api = FakeTrayApi(notification_error=notification_error)
    tray = NativeTray(
        actions=SimpleQueue(),
        startup_enabled=False,
        notifications=notifications,
        icon_path=Path("assets/icon.ico"),
        api=api,
    )
    tray.start(timeout=1)
    return api, tray


def test_tray_adds_updates_and_deletes_icon_on_owner_thread(tmp_path):
    api = FakeTrayApi()
    tray = NativeTray(
        actions=SimpleQueue(),
        startup_enabled=False,
        notifications=True,
        icon_path=tmp_path / "icon.ico",
        api=api,
    )
    caller = threading.get_ident()
    tray.start(timeout=1)
    tray.post_update(TrayUpdate(locked=True))
    api.wait_until(lambda: len(api.modify_calls) == 1)
    tray.stop(timeout=1)
    assert api.add_threads == api.modify_threads == api.delete_threads
    assert api.add_threads[0] != caller
    assert api.destroy_icon_calls == [api.icon_handle]


def test_explorer_restart_readds_current_icon():
    api, tray = started_tray()
    first_count = len(api.add_calls)
    first_version_count = len(api.set_version_calls)
    api.emit_taskbar_created()
    api.wait_until(lambda: len(api.set_version_calls) == first_version_count + 1)
    assert len(api.add_calls) == first_count + 1
    assert len(api.set_version_calls) == first_version_count + 1
    tray.stop(timeout=1)


def test_state_notification_updates_tooltip_and_optional_balloon():
    api, tray = started_tray(notifications=True)
    tray.post_update(TrayUpdate(locked=True, reason="toggle"))
    api.wait_until(lambda: api.notification_calls)
    assert api.modify_calls[-1].tooltip == "CatLocker — Keyboard Locked"
    assert api.notification_calls[-1].message == "Cat Mode ON — Keyboard Locked"
    tray.stop(timeout=1)


def test_balloon_failure_is_cosmetic_and_keeps_updated_state():
    api, tray = started_tray(
        notifications=True, notification_error=OSError("balloon failed")
    )
    tray.post_update(TrayUpdate(locked=True, reason="toggle"))
    api.wait_until(lambda: api.notification_attempts == 1)
    assert api.modify_calls[-1].tooltip == "CatLocker — Keyboard Locked"
    assert tray.is_alive()
    tray.post_update(TrayUpdate(locked=False))
    api.wait_until(
        lambda: api.modify_calls[-1].tooltip == "CatLocker — Keyboard Unlocked"
    )
    tray.stop(timeout=1)


def test_notifications_disabled_produces_no_balloon():
    api, tray = started_tray(notifications=False)
    tray.post_update(TrayUpdate(locked=True, reason="toggle"))
    api.wait_until(lambda: tray.state.locked is True)
    assert api.notification_attempts == 0
    tray.stop(timeout=1)


def test_native_menu_selection_only_enqueues_action():
    api, tray = started_tray()
    api.menu_selection = MenuCommand.UNLOCK
    api.emit_menu_click()
    api.wait_until(lambda: api.show_menu_calls)
    assert tray.actions.get_nowait() == TrayAction.UNLOCK
    assert tray.state.locked is False
    tray.stop(timeout=1)


@pytest.mark.parametrize(
    "failure",
    ["create_window_error", "taskbar_error", "add_error", "set_version_error"],
)
def test_startup_failures_destroy_loaded_icon(failure, tmp_path):
    api = FakeTrayApi(**{failure: OSError(failure)})
    tray = NativeTray(
        actions=SimpleQueue(),
        startup_enabled=False,
        notifications=True,
        icon_path=tmp_path / "icon.ico",
        api=api,
    )
    with pytest.raises(OSError, match=failure):
        tray.start(timeout=1)
    assert api.destroy_icon_calls == [api.icon_handle]


def test_load_failure_does_not_destroy_unowned_icon(tmp_path):
    api = FakeTrayApi(load_error=OSError("load failed"))
    tray = NativeTray(
        actions=SimpleQueue(),
        startup_enabled=False,
        notifications=True,
        icon_path=tmp_path / "icon.ico",
        api=api,
    )
    with pytest.raises(OSError, match="load failed"):
        tray.start(timeout=1)
    assert api.destroy_icon_calls == []


def test_message_loop_error_deletes_icon_best_effort(tmp_path):
    api = FakeTrayApi(
        message_loop_error=OSError("message loop failed"),
        delete_error=OSError("delete failed"),
    )
    tray = NativeTray(
        actions=SimpleQueue(),
        startup_enabled=False,
        notifications=True,
        icon_path=tmp_path / "icon.ico",
        api=api,
    )
    tray.start(timeout=1)
    api.wait_until(lambda: len(api.delete_calls) == 1)
    api.wait_until(lambda: not tray.is_alive())
    assert api.destroy_icon_calls == [api.icon_handle]


def test_notifications_preference_is_observed_on_owner_thread():
    api, tray = started_tray(notifications=True)
    tray.post_notifications_enabled(False)
    api.wait_until(lambda: tray.state.notifications_enabled is False)
    assert tray.state.notifications_enabled is False
    tray.stop(timeout=1)


def test_emergency_force_remove_icon_is_idempotent_and_retains_presentation():
    api, tray = started_tray()

    tray.force_remove_icon()
    tray.force_remove_icon()

    assert len(api.delete_calls) == 1
    tray.post_quit()
    tray.post_quit()
    api.wait_until(lambda: not tray.is_alive())


def test_burst_updates_preserve_every_partial_field():
    api, tray = started_tray(notifications=True)
    try:
        tray._updates.put(TrayUpdate(notifications_enabled=False))
        tray._updates.put(TrayUpdate(startup_enabled=True))
        tray._updates.put(TrayUpdate(locked=True, reason="toggle"))
        tray._drain_updates()

        assert tray.state.locked is True
        assert tray.state.startup_enabled is True
        assert tray.state.notifications_enabled is False
        assert tray.state.tooltip == "CatLocker — Keyboard Locked"
        assert api.notification_attempts == 0
    finally:
        tray.stop(timeout=1)


def test_burst_uses_latest_lock_notification_and_avoids_redundant_modify():
    api, tray = started_tray(notifications=True)
    try:
        tray._updates.put(TrayUpdate(locked=True, reason="toggle"))
        tray._updates.put(TrayUpdate(startup_enabled=True))
        tray._updates.put(TrayUpdate(locked=False, reason="emergency"))
        tray._drain_updates()

        assert tray.state.locked is False
        assert tray.state.startup_enabled is True
        assert len(api.modify_calls) == 0
        assert len(api.notification_calls) == 1
        assert api.notification_calls[0].message == "Cat Mode OFF — Keyboard Unlocked"
    finally:
        tray.stop(timeout=1)


def test_later_lock_update_replaces_earlier_custom_tooltip():
    api, tray = started_tray()
    try:
        tray._updates.put(TrayUpdate(tooltip="custom"))
        tray._updates.put(TrayUpdate(locked=True))
        tray._drain_updates()

        assert tray.state.tooltip == "CatLocker — Keyboard Locked"
    finally:
        tray.stop(timeout=1)
