from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import hotkeys
import pytest
import settings_window as settings_window_module

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
from tray import TrayStopped


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
    notification_error=None,
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
        if notification_error is not None:
            raise notification_error

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


def test_coordinator_exposes_live_recording_text_and_clears_after_completion():
    coordinator = make_coordinator([])
    assert coordinator.recording_text == ""

    assert coordinator.begin_recording() is True
    assert coordinator.recording_text == ""
    coordinator.record_keydown(VK_LCONTROL)
    assert coordinator.recording_text == "Ctrl"
    coordinator.record_keydown(hotkeys.VK_LSHIFT)
    assert coordinator.recording_text == "Ctrl+Shift"
    shortcut = coordinator.record_keydown(0x4B)

    assert shortcut.canonical == "Ctrl+Shift+K"
    assert coordinator.recording_text == ""


def test_recording_preview_requires_new_trigger_after_releasing_unknown_extra_key():
    coordinator = make_coordinator([])
    coordinator.begin_recording()
    coordinator.record_keydown(VK_LCONTROL)
    coordinator.record_keydown(0xFF)
    coordinator.record_keydown(0x4B)

    assert coordinator.recording is True
    assert coordinator.recording_text == "Ctrl+K+VK_FF"

    coordinator.record_keyup(0xFF)

    assert coordinator.recording is True
    assert coordinator.recording_text == "Ctrl+K"
    coordinator.record_keyup(0x4B)
    assert coordinator.recording is True

    shortcut = coordinator.record_keydown(0x4B)

    assert shortcut.canonical == "Ctrl+K"
    assert coordinator.recording is False


@pytest.mark.parametrize(
    ("keysym", "expected"),
    [
        ("Control_L", hotkeys.VK_LCONTROL),
        ("Control_R", hotkeys.VK_RCONTROL),
        ("Shift_L", hotkeys.VK_LSHIFT),
        ("Shift_R", hotkeys.VK_RSHIFT),
        ("Alt_L", hotkeys.VK_LMENU),
        ("Alt_R", hotkeys.VK_RMENU),
    ],
)
def test_normalize_tk_event_maps_left_and_right_modifier_keysyms(keysym, expected):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(None, keysym=keysym)) == expected


@pytest.mark.parametrize(
    ("keysym", "expected"),
    [
        ("Win_L", hotkeys.VK_LWIN),
        ("Win_R", hotkeys.VK_RWIN),
        ("Super_L", hotkeys.VK_LWIN),
        ("Super_R", hotkeys.VK_RWIN),
        ("Meta_L", hotkeys.VK_LWIN),
        ("Meta_R", hotkeys.VK_RWIN),
    ],
)
def test_normalize_tk_event_maps_windows_modifier_aliases(keysym, expected):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(None, keysym=keysym)) == expected


@pytest.mark.parametrize(
    ("keysym", "expected"),
    [
        ("Shift", hotkeys.VK_LSHIFT),
        ("Control", hotkeys.VK_LCONTROL),
        ("Ctrl", hotkeys.VK_LCONTROL),
        ("Alt", hotkeys.VK_LMENU),
        ("Win", hotkeys.VK_LWIN),
        ("Super", hotkeys.VK_LWIN),
        ("Meta", hotkeys.VK_LWIN),
    ],
)
def test_normalize_tk_event_maps_bare_modifier_aliases(keysym, expected):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(None, keysym=keysym)) == expected


@pytest.mark.parametrize(
    ("keycode", "expected"),
    [
        (0x10, hotkeys.VK_LSHIFT),
        (0x11, hotkeys.VK_LCONTROL),
        (0x12, hotkeys.VK_LMENU),
    ],
)
def test_normalize_tk_event_maps_generic_modifier_keycodes(keycode, expected):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(keycode, keysym="")) == expected


def test_normalize_tk_event_prefers_recognized_keysym_over_conflicting_keycode():
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(0x4B, keysym=" control_l ")) == hotkeys.VK_LCONTROL
    assert normalizer(FakeTkEvent("malformed", keysym="Control_L")) == hotkeys.VK_LCONTROL
    assert normalizer(SimpleNamespace(keysym="Control_L")) == hotkeys.VK_LCONTROL


@pytest.mark.parametrize(
    ("keysym", "keycode", "expected"),
    [
        ("  sHiFt_r  ", 0xFF, hotkeys.VK_RSHIFT),
        ("  cTrL  ", 0xFF, hotkeys.VK_LCONTROL),
        ("ignored", 0x4B, 0x4B),
    ],
)
def test_normalize_tk_event_normalizes_keysym_and_passes_through_triggers(
    keysym,
    keycode,
    expected,
):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(keycode, keysym=keysym)) == expected


@pytest.mark.parametrize(
    "keycode",
    [hotkeys.VK_LCONTROL, hotkeys.VK_RCONTROL, hotkeys.VK_LWIN, hotkeys.VK_RWIN, 0x4B],
)
def test_normalize_tk_event_passes_through_existing_virtual_key_values(keycode):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(keycode, keysym="")) == keycode


@pytest.mark.parametrize("keycode", [None, "not-a-number"])
def test_normalize_tk_event_rejects_invalid_fallback_keycodes(keycode):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    with pytest.raises(ValueError):
        normalizer(FakeTkEvent(keycode, keysym=""))


class FakeTkEvent:
    def __init__(self, keycode, keysym="ignored"):
        self.keycode = keycode
        self.keysym = keysym


class FakeTkWidget:
    def __init__(self, *args, **kwargs):
        self.configured = {}
        self.bindings = {}
        self._next_binding = 1

    def pack(self, *args, **kwargs):
        return None

    def place(self, *args, **kwargs):
        return None

    def configure(self, **kwargs):
        self.configured.update(kwargs)

    config = configure

    def bind(self, sequence, callback):
        binding_id = f"binding-{self._next_binding}"
        self._next_binding += 1
        self.bindings[(sequence, binding_id)] = callback
        return binding_id

    def unbind(self, sequence, binding_id=None):
        if binding_id is None:
            self.bindings = {
                key: value
                for key, value in self.bindings.items()
                if key[0] != sequence
            }
        else:
            self.bindings.pop((sequence, binding_id), None)

    def focus_set(self):
        return None


class FakeTkVariable:
    def __init__(self, master=None, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeTkToplevel(FakeTkWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.protocols = {}
        self.withdrawn = False
        self.destroyed = False
        self.deiconify_count = 0

    def title(self, value):
        self.window_title = value

    def withdraw(self):
        self.withdrawn = True

    def deiconify(self):
        self.withdrawn = False
        self.deiconify_count += 1

    def lift(self):
        return None

    def protocol(self, name, callback):
        self.protocols[name] = callback

    def destroy(self):
        self.destroyed = True


def fake_tk_module():
    tk = ModuleType("tkinter")
    tk.Toplevel = FakeTkToplevel
    tk.Frame = FakeTkWidget
    tk.Label = FakeTkWidget
    tk.Entry = FakeTkWidget
    tk.Button = FakeTkWidget
    tk.Checkbutton = FakeTkWidget
    tk.StringVar = FakeTkVariable
    tk.BooleanVar = FakeTkVariable
    messagebox = ModuleType("tkinter.messagebox")
    messagebox.askyesno = lambda *args, **kwargs: True
    messagebox.showerror = lambda *args, **kwargs: None
    tk.messagebox = messagebox
    return tk, messagebox


def make_settings_window(
    monkeypatch,
    *,
    on_engine_unhealthy=None,
    on_startup_result=None,
    coordinator=None,
):
    import settings_window as settings_window_module

    tk, messagebox = fake_tk_module()
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", messagebox)
    coordinator = coordinator or make_coordinator([])
    return settings_window_module.SettingsWindow(
        object(),
        coordinator,
        on_engine_unhealthy=on_engine_unhealthy,
        on_startup_result=on_startup_result,
    )


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

    result = view.set_startup_enabled(False)

    assert result == StartupUpdateResult(True, startup.write_error)
    assert view.startup_enabled is True


def test_settings_window_installs_permanent_close_protocol(monkeypatch):
    window = make_settings_window(monkeypatch)

    assert window.window.protocols["WM_DELETE_WINDOW"] == window._close


def test_settings_window_is_reusable_after_cancel_and_titlebar_close(monkeypatch):
    window = make_settings_window(monkeypatch)
    window.show()
    window.hotkey_var.set("K")
    window._close()
    assert window.window.destroyed is False
    assert window.window.withdrawn is True

    window.show()
    assert window.hotkey_var.get() == "F24"
    window.window.protocols["WM_DELETE_WINDOW"]()
    assert window.window.destroyed is False
    assert window.window.withdrawn is True

    window.show()
    assert window.hotkey_var.get() == "F24"
    assert window.window.deiconify_count == 3


@pytest.mark.parametrize(
    "handler",
    [
        "record",
        "save",
        "key_press",
        "focus_out",
        "close",
        "lock_state",
    ],
)
def test_engine_unhealthy_routes_every_settings_handler_to_fatal_shutdown(
    monkeypatch,
    handler,
):
    fatal_errors = []
    window = make_settings_window(
        monkeypatch,
        on_engine_unhealthy=fatal_errors.append,
    )
    error = EngineUnhealthy(f"{handler} failed")
    view_method = {
        "record": "begin_recording",
        "save": "save",
        "key_press": "on_key_press",
        "focus_out": "on_focus_out",
        "close": "on_close",
        "lock_state": "on_lock_state",
    }[handler]
    monkeypatch.setattr(
        window.view,
        view_method,
        lambda *args, **kwargs: (_ for _ in ()).throw(error),
    )

    if handler == "record":
        window._record()
    elif handler == "save":
        window._save()
    elif handler == "key_press":
        window._on_key_press(FakeTkEvent(0x4B))
    elif handler == "focus_out":
        window._on_focus_out()
    elif handler == "close":
        window._close()
    else:
        window.on_lock_state(True)

    assert fatal_errors == [error]


def test_settings_startup_change_publishes_result_to_application(monkeypatch):
    results = []
    window = make_settings_window(
        monkeypatch,
        on_startup_result=results.append,
    )
    window.startup_var.set(True)
    window._startup_changed()

    assert results == [StartupUpdateResult(True)]
    assert window.view.startup_enabled is True


def test_tray_loss_during_settings_save_routes_to_fatal_callback(monkeypatch):
    error = TrayStopped("tray disappeared")
    coordinator = make_coordinator([], notification_error=error)
    fatal_errors = []
    window = make_settings_window(
        monkeypatch,
        coordinator=coordinator,
        on_engine_unhealthy=fatal_errors.append,
    )
    window.hotkey_var.set("K")
    window.notifications_var.set(False)

    window._save()

    assert fatal_errors == [error]


def test_settings_startup_failure_publishes_actual_state(monkeypatch):
    error = PermissionError("registry denied")
    coordinator = make_coordinator(
        [],
        startup=FakeStartupRegistry(actual=True, write_error=error),
    )
    results = []
    window = make_settings_window(
        monkeypatch,
        coordinator=coordinator,
        on_startup_result=results.append,
    )
    window.startup_var.set(False)
    window._startup_changed()

    assert results == [StartupUpdateResult(True, error)]
    assert window.startup_var.get() is True


def test_settings_startup_unknown_state_preserves_value(monkeypatch):
    error = PermissionError("registry unavailable")
    coordinator = make_coordinator(
        [],
        startup=FakeStartupRegistry(
            actual=False,
            write_error=error,
            refresh_error=OSError("read denied"),
        ),
    )
    results = []
    window = make_settings_window(
        monkeypatch,
        coordinator=coordinator,
        on_startup_result=results.append,
    )
    window.startup_var.set(True)
    window._startup_changed()

    assert results == [StartupUpdateResult(None, error)]
    assert window.view.startup_enabled is False
    assert window.startup_var.get() is False
