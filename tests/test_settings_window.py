from __future__ import annotations

import pytest

from controller import EngineUnhealthy
from hotkeys import ShortcutError, VK_LCONTROL
from settings import AppSettings
from settings_window import (
    SettingsCoordinator,
    SettingsLocked,
    SettingsViewModel,
    StartupUpdateResult,
    WarningDeclined,
)


_UNSET = object()


class FakeController:
    def __init__(
        self,
        calls,
        *,
        locked: bool = False,
        replace_result=1,
        rollback_error: BaseException | None = None,
        rollback_result=_UNSET,
        enter_recording_result: bool = True,
        enter_recording_error: BaseException | None = None,
        exit_recording_error: BaseException | None = None,
    ):
        self.calls = calls
        self.locked = locked
        self.replace_result = replace_result
        self.rollback_error = rollback_error
        self.rollback_result = rollback_result
        self.enter_recording_result = enter_recording_result
        self.enter_recording_error = enter_recording_error
        self.exit_recording_error = exit_recording_error
        self.replace_count = 0
        self.fail_open_calls = 0

    def replace_shortcut(self, shortcut):
        self.replace_count += 1
        self.calls.append(("replace", shortcut.canonical))
        if self.replace_count > 1 and self.rollback_error is not None:
            raise self.rollback_error
        if self.replace_count > 1 and self.rollback_result is not _UNSET:
            return self.rollback_result
        return self.replace_result

    def enter_recording(self):
        self.calls.append(("enter_recording", None))
        if self.enter_recording_error is not None:
            raise self.enter_recording_error
        return self.enter_recording_result

    def exit_recording(self):
        self.calls.append(("exit_recording", None))
        if self.exit_recording_error is not None:
            raise self.exit_recording_error
        return True

    def enter_fail_open(self):
        self.fail_open_calls += 1


class FakeStartupRegistry:
    def __init__(self, *, actual=False, write_error=None, refresh_error=None):
        self.actual = actual
        self.write_error = write_error
        self.refresh_error = refresh_error
        self.calls = []

    def set_enabled(self, enabled):
        self.calls.append(("set_enabled", enabled))
        if self.write_error is not None:
            raise self.write_error
        self.actual = enabled

    def is_enabled(self):
        self.calls.append(("is_enabled", None))
        if self.refresh_error is not None:
            raise self.refresh_error
        return self.actual

def make_coordinator(
    calls,
    *,
    current=AppSettings(),
    locked=False,
    persist_error=None,
    rollback_error=None,
    rollback_result=_UNSET,
    startup=None,
    controller_options=None,
):
    controller = FakeController(
        calls,
        locked=locked,
        rollback_error=rollback_error,
        rollback_result=rollback_result,
        **(controller_options or {}),
    )

    def persist(settings):
        calls.append(("persist", settings))
        if persist_error is not None:
            raise persist_error

    def apply_notifications(enabled):
        calls.append(("notifications", enabled))

    coordinator = SettingsCoordinator(
        current,
        controller,
        persist,
        startup or FakeStartupRegistry(),
        apply_notifications,
    )
    coordinator.controller = controller
    return coordinator


def test_save_validates_replaces_persists_then_applies_notifications():
    calls = []
    coordinator = make_coordinator(calls, current=AppSettings())
    saved = coordinator.save(
        "shift+ctrl+k",
        False,
        confirm_warning=lambda messages: True,
    )
    assert saved == AppSettings("Ctrl+Shift+K", False)
    assert calls == [
        ("replace", "Ctrl+Shift+K"),
        ("persist", AppSettings("Ctrl+Shift+K", False)),
        ("notifications", False),
    ]


def test_persistence_failure_rolls_back_shortcut_and_live_notifications():
    calls = []
    coordinator = make_coordinator(calls, persist_error=OSError("disk full"))
    with pytest.raises(OSError, match="disk full"):
        coordinator.save("K", False, confirm_warning=lambda messages: True)
    assert calls == [
        ("replace", "K"),
        ("persist", AppSettings("K", False)),
        ("replace", "F24"),
    ]
    assert coordinator.current == AppSettings("F24", True)


def test_rollback_timeout_propagates_engine_unhealthy():
    coordinator = make_coordinator(
        [],
        persist_error=OSError("disk full"),
        rollback_error=EngineUnhealthy("stalled"),
    )
    with pytest.raises(EngineUnhealthy, match="stalled"):
        coordinator.save("K", False, confirm_warning=lambda messages: True)


def test_normal_rollback_rejection_enters_fail_open():
    coordinator = make_coordinator(
        [],
        persist_error=OSError("disk full"),
        rollback_result=None,
    )
    with pytest.raises(EngineUnhealthy, match="rollback rejected"):
        coordinator.save("K", False, confirm_warning=lambda messages: True)
    assert coordinator.controller.fail_open_calls == 1


def test_locked_state_rejects_save_before_validation_or_io():
    calls = []
    coordinator = make_coordinator(calls, locked=True)
    with pytest.raises(SettingsLocked):
        coordinator.save("K", False, confirm_warning=lambda messages: True)
    assert calls == []


def test_warning_requires_explicit_confirmation():
    coordinator = make_coordinator([], current=AppSettings())
    with pytest.raises(WarningDeclined):
        coordinator.save("Win+L", True, confirm_warning=lambda messages: False)


def test_save_while_recording_exits_before_replacement():
    calls = []
    coordinator = make_coordinator(calls, current=AppSettings())
    coordinator.begin_recording()
    calls.clear()
    coordinator.save("K", True, confirm_warning=lambda messages: True)
    assert calls[:2] == [("exit_recording", None), ("replace", "K")]


def test_startup_write_failure_refreshes_actual_state_and_returns_primary_error():
    startup = FakeStartupRegistry(
        actual=True,
        write_error=PermissionError("denied"),
    )
    coordinator = make_coordinator([], startup=startup)

    result = coordinator.set_startup_enabled(False)

    assert result == StartupUpdateResult(True, startup.write_error)
    assert startup.calls == [("set_enabled", False), ("is_enabled", None)]


def test_startup_refresh_failure_does_not_replace_write_error():
    write_error = PermissionError("write denied")
    startup = FakeStartupRegistry(
        write_error=write_error,
        refresh_error=OSError("read denied"),
    )
    coordinator = make_coordinator([], startup=startup)

    result = coordinator.set_startup_enabled(True)

    assert result == StartupUpdateResult(None, write_error)


def test_successful_startup_write_with_refresh_failure_returns_refresh_error():
    refresh_error = OSError("read denied")
    startup = FakeStartupRegistry(refresh_error=refresh_error)
    coordinator = make_coordinator([], startup=startup)

    result = coordinator.set_startup_enabled(True)

    assert result == StartupUpdateResult(None, refresh_error)


def test_startup_update_never_persists_app_settings():
    calls = []
    startup = FakeStartupRegistry()
    coordinator = make_coordinator(calls, startup=startup)

    coordinator.set_startup_enabled(True)

    assert not any(call[0] == "persist" for call in calls)


def test_recording_enters_only_after_controller_ack_and_ignores_repeat():
    calls = []
    coordinator = make_coordinator(calls)

    assert coordinator.begin_recording() is True
    assert coordinator.record_keydown(VK_LCONTROL) is None
    assert coordinator.record_keydown(VK_LCONTROL) is None
    shortcut = coordinator.record_keydown(0x4B)

    assert shortcut.canonical == "Ctrl+K"
    assert calls == [
        ("enter_recording", None),
        ("exit_recording", None),
    ]
    assert coordinator.recording is False


def test_recording_rejection_does_not_activate_local_capture():
    coordinator = make_coordinator(
        [],
        controller_options={"enter_recording_result": False},
    )

    assert coordinator.begin_recording() is False
    assert coordinator.recording is False


def test_end_recording_exits_controller_before_clearing_capture():
    calls = []
    coordinator = make_coordinator(calls)
    coordinator.begin_recording()
    calls.clear()

    coordinator.end_recording()

    assert calls == [("exit_recording", None)]
    assert coordinator.recording is False


def test_recording_engine_failure_clears_local_state_and_reraises():
    error = EngineUnhealthy("stalled")
    coordinator = make_coordinator(
        [],
        controller_options={"enter_recording_error": error},
    )

    with pytest.raises(EngineUnhealthy, match="stalled"):
        coordinator.begin_recording()

    assert coordinator.recording is False


class FakeTkEvent:
    def __init__(self, keycode, keysym="ignored"):
        self.keycode = keycode
        self.keysym = keysym


def test_view_record_display_changes_only_after_coordinator_acceptance():
    rejected = make_coordinator(
        [],
        controller_options={"enter_recording_result": False},
    )
    view = SettingsViewModel(rejected)

    assert view.begin_recording() is False
    assert view.recording is False
    assert view.record_label == "Record shortcut"

    accepted = make_coordinator([])
    view = SettingsViewModel(accepted)
    assert view.begin_recording() is True
    assert view.recording is True
    assert view.record_label == "Press a shortcut..."


def test_view_forwards_windows_numeric_keycode_as_vk():
    calls = []
    coordinator = make_coordinator(calls)
    view = SettingsViewModel(coordinator)
    view.begin_recording()

    view.on_key_press(FakeTkEvent(0x4B, keysym="not-the-vk"))

    assert view.hotkey_text == "K"
    assert view.recording is False


def test_view_focus_loss_and_close_exit_recording():
    calls = []
    coordinator = make_coordinator(calls)
    view = SettingsViewModel(coordinator)

    view.begin_recording()
    view.on_focus_out()
    assert view.recording is False
    assert calls[-1] == ("exit_recording", None)

    view.begin_recording()
    view.on_close()
    assert view.recording is False
    assert calls[-1] == ("exit_recording", None)


def test_view_save_is_disabled_when_controller_reports_locked():
    coordinator = make_coordinator([], locked=True)
    view = SettingsViewModel(coordinator)

    assert view.save_enabled is False
    assert view.record_enabled is False


@pytest.mark.parametrize("persist_error", [ShortcutError("invalid"), OSError("disk full")])
def test_view_validation_and_persistence_errors_preserve_displayed_canonical_hotkey(
    persist_error,
):
    coordinator = make_coordinator([], persist_error=persist_error)
    view = SettingsViewModel(coordinator)
    view.hotkey_text = "K"

    with pytest.raises(type(persist_error)):
        view.save(confirm_warning=lambda messages: True)

    assert view.hotkey_text == "F24"


def test_view_startup_error_refreshes_checkbox_from_actual_registry_state():
    startup = FakeStartupRegistry(
        actual=True,
        write_error=PermissionError("denied"),
    )
    view = SettingsViewModel(make_coordinator([], startup=startup), startup_enabled=False)

    with pytest.raises(PermissionError, match="denied"):
        view.set_startup_enabled(False)

    assert view.startup_enabled is True
