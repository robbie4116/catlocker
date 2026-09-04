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
        exit_recording_result: bool = True,
        exit_recording_error: BaseException | None = None,
    ):
        self.calls = calls
        self.locked = locked
        self.replace_result = replace_result
        self.rollback_error = rollback_error
        self.rollback_result = rollback_result
        self.enter_recording_result = enter_recording_result
        self.enter_recording_error = enter_recording_error
        self.exit_recording_result = exit_recording_result
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
        return self.exit_recording_result

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
    [
        hotkeys.VK_LCONTROL,
        hotkeys.VK_RCONTROL,
        hotkeys.VK_LSHIFT,
        hotkeys.VK_RSHIFT,
        hotkeys.VK_LMENU,
        hotkeys.VK_RMENU,
        hotkeys.VK_LWIN,
        hotkeys.VK_RWIN,
        0x4B,
    ],
)
def test_normalize_tk_event_passes_through_existing_virtual_key_values(keycode):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    assert normalizer(FakeTkEvent(keycode, keysym="")) == keycode


@pytest.mark.parametrize("keycode", [None, "not-a-number"])
def test_normalize_tk_event_rejects_invalid_fallback_keycodes(keycode):
    normalizer = getattr(settings_window_module, "normalize_tk_event")

    with pytest.raises(ValueError) as exc_info:
        normalizer(FakeTkEvent(keycode, keysym=""))

    assert type(exc_info.value).__name__ == "MalformedTkEvent"


class FakeTkEvent:
    def __init__(self, keycode, keysym="ignored"):
        self.keycode = keycode
        self.keysym = keysym


class FakeTkWidget:
    def __init__(self, *args, **kwargs):
        self.configured = dict(kwargs)
        self.bindings = {}
        self._next_binding = 1
        self.focus_set_calls = 0
        self.textvariable = kwargs.get("textvariable")

    def pack(self, *args, **kwargs):
        return None

    def place(self, *args, **kwargs):
        return None

    def configure(self, **kwargs):
        self.configured.update(kwargs)
        if "textvariable" in kwargs:
            self.textvariable = kwargs["textvariable"]

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
        self.focus_set_calls += 1
        return None

    def _apply_default_class_binding(self, sequence, event):
        if sequence != "<KeyPress>":
            return None
        if self.configured.get("state", "normal") != "normal":
            return None
        if self.textvariable is None:
            return None
        if not isinstance(event.keysym, str) or len(event.keysym) != 1:
            return None
        self.textvariable.set(f"{self.textvariable.get()}{event.keysym}")
        return None


class FakeTkVariable:
    def __init__(self, master=None, value=None):
        self.value = value
        self.set_calls = []

    def get(self):
        return self.value

    def set(self, value):
        self.set_calls.append(value)
        self.value = value


def dispatch_child_event(child, parent, sequence, event):
    child_or_parent_returned_break = False
    for (bound_sequence, _binding_id), callback in child.bindings.items():
        if bound_sequence == sequence:
            if callback(event) == "break":
                child_or_parent_returned_break = True
                break
    for (bound_sequence, _binding_id), callback in parent.bindings.items():
        if child_or_parent_returned_break:
            break
        if bound_sequence == sequence and callback(event) == "break":
            child_or_parent_returned_break = True
            break
    if child_or_parent_returned_break:
        return "break"
    child._apply_default_class_binding(sequence, event)
    return None


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


def test_view_begin_recording_clears_display():
    coordinator = make_coordinator([])
    view = SettingsViewModel(coordinator)
    view.hotkey_text = "Ctrl+K"

    assert view.begin_recording() is True
    assert view.hotkey_text == ""

    rejected = make_coordinator(
        [],
        controller_options={"enter_recording_result": False},
    )
    rejected_view = SettingsViewModel(rejected)
    rejected_view.hotkey_text = "Ctrl+K"

    assert rejected_view.begin_recording() is False
    assert rejected_view.hotkey_text == "Ctrl+K"


def test_view_updates_live_preview():
    coordinator = make_coordinator([])
    view = SettingsViewModel(coordinator)

    assert view.begin_recording() is True
    assert view.hotkey_text == ""
    view.on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))
    assert view.hotkey_text == "Ctrl"
    view.on_key_press(FakeTkEvent(0xFF, keysym="Shift_L"))
    assert view.hotkey_text == "Ctrl+Shift"
    view.on_key_release(FakeTkEvent(0xFE, keysym="Shift_L"))
    assert view.hotkey_text == "Ctrl"
    view.on_key_press(FakeTkEvent(0x4B, keysym="k"))

    assert view.hotkey_text == "Ctrl+K"
    assert view.recording is False


def test_view_save_is_disabled_during_partial_recording():
    coordinator = make_coordinator([])
    view = SettingsViewModel(coordinator)

    assert view.save_enabled is True
    view.begin_recording()
    view.on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))

    assert view.save_enabled is False

    view.on_key_press(FakeTkEvent(0x4B, keysym="k"))
    assert view.save_enabled is True


def test_view_new_recording_replaces_unsaved_candidate():
    coordinator = make_coordinator([])
    view = SettingsViewModel(coordinator)

    view.begin_recording()
    view.on_key_press(FakeTkEvent(0x4B, keysym="k"))
    assert view.hotkey_text == "K"

    view.begin_recording()
    assert view.hotkey_text == ""
    view.cancel_recording()

    assert view.hotkey_text == "F24"


def test_view_cancel_restores_accepted():
    coordinator = make_coordinator([])
    view = SettingsViewModel(coordinator)

    view.begin_recording()
    view.on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))
    assert view.hotkey_text == "Ctrl"

    view.cancel_recording()

    assert view.hotkey_text == "F24"
    assert view.recording is False


def test_view_false_exit_preserves_draft():
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_result": False},
    )
    view = SettingsViewModel(coordinator)

    view.begin_recording()
    view.on_key_press(FakeTkEvent(0x4B, keysym="k"))

    assert view.hotkey_text == "K"
    assert view.recording is False


@pytest.mark.parametrize("completion_error", [EngineUnhealthy("stalled"), OSError("failed")])
def test_view_completion_failure_restores_accepted(completion_error):
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_error": completion_error},
    )
    view = SettingsViewModel(coordinator)
    view.hotkey_text = "Ctrl+K"
    view._accepted_hotkey = "F24"
    view.begin_recording()

    with pytest.raises(type(completion_error), match=str(completion_error)):
        view.on_key_press(FakeTkEvent(0x4B, keysym="k"))

    assert view.hotkey_text == "F24"
    assert view.recording is False


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


def test_settings_window_recording_focuses_entry_and_binds_both(monkeypatch):
    window = make_settings_window(monkeypatch)
    window.hotkey_var.set_calls.clear()

    window._record()

    assert window.hotkey_entry.focus_set_calls == 1
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_var.get() == ""
    assert "" in window.hotkey_var.set_calls
    assert {sequence for sequence, _ in window.hotkey_entry.bindings} == {
        "<KeyPress>",
        "<KeyRelease>",
        "<FocusOut>",
    }
    assert {sequence for sequence, _ in window.window.bindings} == {
        "<KeyPress>",
        "<KeyRelease>",
    }
    assert window.window.protocols["WM_DELETE_WINDOW"] == window._close


def test_settings_window_rejected_recording_preserves_value_and_bindings(monkeypatch):
    coordinator = make_coordinator(
        [],
        controller_options={"enter_recording_result": False},
    )
    window = make_settings_window(monkeypatch, coordinator=coordinator)
    window.hotkey_var.set("Ctrl+K")
    window.hotkey_var.set_calls.clear()

    window._record()

    assert window.hotkey_var.get() == "Ctrl+K"
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.hotkey_entry.focus_set_calls == 0
    assert window.hotkey_var.set_calls == []


def test_settings_window_shortcut_entry_is_readonly_when_unlocked(monkeypatch):
    window = make_settings_window(monkeypatch)

    assert window.hotkey_entry.configured["state"] == "readonly"

    window._record()
    assert window.hotkey_entry.configured["state"] == "readonly"

    assert (
        dispatch_child_event(
            window.hotkey_entry,
            window.window,
            "<KeyPress>",
            FakeTkEvent(0xFF, keysym="Control_L"),
        )
        == "break"
    )
    assert window.hotkey_entry.configured["state"] == "readonly"

    assert (
        dispatch_child_event(
            window.hotkey_entry,
            window.window,
            "<KeyPress>",
            FakeTkEvent(0x4B, keysym="k"),
        )
        == "break"
    )
    assert window.hotkey_entry.configured["state"] == "readonly"

    window._close()
    assert window.hotkey_entry.configured["state"] == "readonly"


def test_settings_window_shortcut_entry_is_disabled_when_locked(monkeypatch):
    window = make_settings_window(monkeypatch, coordinator=make_coordinator([], locked=True))

    assert window.hotkey_entry.configured["state"] == "disabled"


def test_settings_window_readonly_entry_rejects_direct_text_but_accepts_programmatic_updates(
    monkeypatch,
):
    window = make_settings_window(monkeypatch)

    dispatch_child_event(
        window.hotkey_entry,
        window.window,
        "<KeyPress>",
        FakeTkEvent(0x4B, keysym="k"),
    )

    assert window.hotkey_var.get() == "F24"

    window.hotkey_var.set("Ctrl+Shift+K")
    assert window.hotkey_var.get() == "Ctrl+Shift+K"


def test_settings_window_save_synchronizes_programmatic_display_value(monkeypatch):
    window = make_settings_window(monkeypatch)

    window.hotkey_var.set("K")
    window._save()

    assert window.view.hotkey_text == "K"
    assert window.view.coordinator.current.toggle_hotkey == "K"


def test_settings_window_dispatch(monkeypatch):
    window = make_settings_window(monkeypatch)
    calls = []
    original = window.view.on_key_press

    def on_key_press(event):
        calls.append(event)
        return original(event)

    window.view.on_key_press = on_key_press
    window._record()

    entry_result = dispatch_child_event(
        window.hotkey_entry,
        window.window,
        "<KeyPress>",
        FakeTkEvent(0xFF, keysym="Control_L"),
    )
    other_child_result = dispatch_child_event(
        FakeTkWidget(),
        window.window,
        "<KeyPress>",
        FakeTkEvent(0xFE, keysym="Shift_L"),
    )

    assert entry_result == "break"
    assert other_child_result == "break"
    assert len(calls) == 2
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.view.hotkey_text == "Ctrl+Shift"


def test_settings_window_handlers_return_break_and_synchronize(monkeypatch):
    window = make_settings_window(monkeypatch)
    window._record()
    window.hotkey_var.set_calls.clear()

    assert (
        dispatch_child_event(
            window.hotkey_entry,
            window.window,
            "<KeyPress>",
            FakeTkEvent(0xFF, keysym="Control_L"),
        )
        == "break"
    )
    assert (
        dispatch_child_event(
            FakeTkWidget(),
            window.window,
            "<KeyPress>",
            FakeTkEvent(0xFF, keysym="Shift_L"),
        )
        == "break"
    )
    assert (
        dispatch_child_event(
            FakeTkWidget(),
            window.window,
            "<KeyRelease>",
            FakeTkEvent(0xFE, keysym="Shift_L"),
        )
        == "break"
    )
    assert (
        dispatch_child_event(
            window.hotkey_entry,
            window.window,
            "<KeyPress>",
            FakeTkEvent(0x4B, keysym="k"),
        )
        == "break"
    )

    assert window.hotkey_var.set_calls == ["Ctrl", "Ctrl+Shift", "Ctrl", "Ctrl+K"]
    assert window.hotkey_var.get() == "Ctrl+K"
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.save_button.configured["state"] == "normal"


def test_settings_window_focus_loss_cancels_active_recording(monkeypatch):
    window = make_settings_window(monkeypatch)
    window.show()
    window._record()
    window._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))

    window._on_focus_out()

    assert window.view.recording is False
    assert window.hotkey_var.get() == "F24"
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.window.withdrawn is False
    assert window.record_button.configured["state"] == "normal"
    assert window.save_button.configured["state"] == "normal"


def test_settings_window_close_discards_unsaved_candidate(monkeypatch):
    window = make_settings_window(monkeypatch)
    window.show()
    window._record()
    window._on_key_press(FakeTkEvent(0x4B, keysym="k"))
    assert window.hotkey_var.get() == "K"

    window._on_focus_out()
    assert window.hotkey_var.get() == "K"

    window._close()

    assert window.hotkey_var.get() == "F24"
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.window.withdrawn is True
    assert window.window.protocols["WM_DELETE_WINDOW"] == window._close


def test_settings_window_malformed_event_is_ignored(monkeypatch):
    window = make_settings_window(monkeypatch)
    window._record()
    window.hotkey_var.set_calls.clear()

    before_press = (
        window.view.hotkey_text,
        window.hotkey_var.get(),
        window.view.recording,
        dict(window.hotkey_entry.bindings),
        dict(window.window.bindings),
    )
    assert window._on_key_press(FakeTkEvent(None, keysym="")) == "break"
    assert (
        window.view.hotkey_text,
        window.hotkey_var.get(),
        window.view.recording,
        dict(window.hotkey_entry.bindings),
        dict(window.window.bindings),
    ) == before_press

    assert window._on_key_press(FakeTkEvent(0xFF, keysym="Control_L")) == "break"
    before_release = (
        window.view.hotkey_text,
        window.hotkey_var.get(),
        window.view.recording,
    )
    assert window._on_key_release(FakeTkEvent("bad", keysym="")) == "break"
    assert (
        window.view.hotkey_text,
        window.hotkey_var.get(),
        window.view.recording,
    ) == before_release
    assert window.view.recording is True


def test_settings_window_controller_value_error_during_completion_is_routed(monkeypatch):
    error = ValueError("completion failed")
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_error": error},
    )
    errors = []
    window = make_settings_window(monkeypatch, coordinator=coordinator)
    window._show_error = errors.append
    window._record()

    assert window._on_key_press(FakeTkEvent(0x4B, keysym="k")) == "break"

    assert errors == [error]
    assert window.hotkey_var.get() == "F24"
    assert window.view.recording is False
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.record_button.configured["state"] == "normal"
    assert window.save_button.configured["state"] == "normal"


def test_settings_window_coordinator_value_error_during_release_is_routed(monkeypatch):
    error = ValueError("release failed")
    coordinator = make_coordinator([])
    original_record_keyup = coordinator.record_keyup

    def record_keyup(vk):
        original_record_keyup(vk)
        raise error

    coordinator.record_keyup = record_keyup
    errors = []
    window = make_settings_window(monkeypatch, coordinator=coordinator)
    window._show_error = errors.append
    window._record()
    window._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))

    assert window._on_key_release(FakeTkEvent(0xFE, keysym="Shift_L")) == "break"

    assert errors == [error]
    assert window.hotkey_var.get() == "F24"
    assert window.view.recording is False
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.record_button.configured["state"] == "normal"
    assert window.save_button.configured["state"] == "normal"


def test_settings_window_binding_is_idempotent(monkeypatch):
    window = make_settings_window(monkeypatch)

    window._bind_recording_events()
    first_entry_bindings = dict(window.hotkey_entry.bindings)
    first_window_bindings = dict(window.window.bindings)
    first_recording_bindings = list(window._recording_bindings)

    window._bind_recording_events()

    assert window.hotkey_entry.bindings == first_entry_bindings
    assert window.window.bindings == first_window_bindings
    assert window._recording_bindings == first_recording_bindings


def test_settings_window_binding_failure_removes_partial_bindings(monkeypatch):
    error = RuntimeError("bind failed")
    window = make_settings_window(monkeypatch)
    original_bind = window.window.bind

    def fail_on_second_bind(sequence, callback):
        if sequence == "<KeyPress>":
            raise error
        return original_bind(sequence, callback)

    monkeypatch.setattr(window.window, "bind", fail_on_second_bind)

    with pytest.raises(RuntimeError, match="bind failed"):
        window._bind_recording_events()

    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window._recording_bindings == []


def test_settings_window_record_binding_failure_cleans_up_and_shows_error(monkeypatch):
    error = RuntimeError("bind failed")
    window = make_settings_window(monkeypatch)
    original_bind = window.window.bind

    def fail_on_second_bind(sequence, callback):
        if sequence == "<KeyPress>":
            raise error
        return original_bind(sequence, callback)

    monkeypatch.setattr(window.window, "bind", fail_on_second_bind)
    errors = []
    window._show_error = errors.append

    window._record()

    assert errors == [error]
    assert window.view.recording is False
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_var.get() == "F24"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.record_button.configured["state"] == "normal"
    assert window.save_button.configured["state"] == "normal"


def test_settings_window_cleanup(monkeypatch):
    completed = make_settings_window(monkeypatch)
    completed._record()
    completed._on_key_press(FakeTkEvent(0x4B, keysym="k"))
    assert completed.hotkey_var.get() == "K"
    assert completed.hotkey_entry.configured["state"] == "readonly"
    assert completed.hotkey_entry.bindings == {}
    assert completed.window.bindings == {}
    assert completed.record_button.configured["state"] == "normal"
    assert completed.save_button.configured["state"] == "normal"

    focused = make_settings_window(monkeypatch)
    focused.show()
    focused._record()
    focused._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))
    focused._on_focus_out()
    assert focused.hotkey_var.get() == "F24"
    assert focused.hotkey_entry.configured["state"] == "readonly"
    assert focused.hotkey_entry.bindings == {}
    assert focused.window.bindings == {}
    assert focused.window.withdrawn is False

    locked = make_settings_window(monkeypatch)
    locked.show()
    locked._record()
    locked._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))
    locked.on_lock_state(True)
    assert locked.hotkey_var.get() == "F24"
    assert locked.hotkey_entry.configured["state"] == "disabled"
    assert locked.hotkey_entry.bindings == {}
    assert locked.window.bindings == {}
    assert locked.window.withdrawn is True
    assert locked.record_button.configured["state"] == "disabled"
    assert locked.save_button.configured["state"] == "disabled"

    closed = make_settings_window(monkeypatch)
    closed._record()
    closed._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))
    closed._close()
    assert closed.hotkey_var.get() == "F24"
    assert closed.hotkey_entry.configured["state"] == "readonly"
    assert closed.hotkey_entry.bindings == {}
    assert closed.window.bindings == {}
    assert closed.window.withdrawn is True


def test_settings_window_false_exit(monkeypatch):
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_result": False},
    )
    fatal_errors = []
    window = make_settings_window(
        monkeypatch,
        coordinator=coordinator,
        on_engine_unhealthy=fatal_errors.append,
    )
    window._record()

    assert window._on_key_press(FakeTkEvent(0x4B, keysym="k")) == "break"

    assert fatal_errors == []
    assert window.hotkey_var.get() == "K"
    assert window.view.recording is False
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}


@pytest.mark.parametrize("error_kind", ["engine", "ordinary"])
def test_settings_window_engine_unhealthy_and_ordinary_exception(monkeypatch, error_kind):
    error = EngineUnhealthy("stalled") if error_kind == "engine" else OSError("failed")
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_error": error},
    )
    fatal_errors = []
    ordinary_errors = []
    window = make_settings_window(
        monkeypatch,
        coordinator=coordinator,
        on_engine_unhealthy=fatal_errors.append,
    )
    window._show_error = ordinary_errors.append
    window._record()

    assert window._on_key_press(FakeTkEvent(0x4B, keysym="k")) == "break"

    assert window.hotkey_var.get() == "F24"
    assert window.view.recording is False
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.window.protocols["WM_DELETE_WINDOW"] == window._close
    if error_kind == "engine":
        assert fatal_errors == [error]
        assert ordinary_errors == []
    else:
        assert fatal_errors == []
        assert ordinary_errors == [error]


def test_settings_window_lock_ordinary_exception(monkeypatch):
    error = OSError("failed")
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_error": error},
    )
    errors = []
    window = make_settings_window(monkeypatch, coordinator=coordinator)
    window._show_error = errors.append
    window._record()
    window._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))

    window.on_lock_state(True)

    assert errors == [error]
    assert window.hotkey_var.get() == "F24"
    assert window.hotkey_entry.configured["state"] == "disabled"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.window.withdrawn is True
    assert window.window.protocols["WM_DELETE_WINDOW"] == window._close
    assert window.record_button.configured["state"] == "disabled"


def test_settings_window_lock_engine_unhealthy(monkeypatch):
    error = EngineUnhealthy("stalled")
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_error": error},
    )
    fatal_errors = []
    window = make_settings_window(
        monkeypatch,
        coordinator=coordinator,
        on_engine_unhealthy=fatal_errors.append,
    )
    window._record()
    window._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))

    window.on_lock_state(True)

    assert fatal_errors == [error]
    assert window.hotkey_var.get() == "F24"
    assert window.hotkey_entry.configured["state"] == "disabled"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.window.withdrawn is True
    assert window.window.protocols["WM_DELETE_WINDOW"] == window._close


@pytest.mark.parametrize("error_kind", ["ordinary", "engine"])
def test_settings_window_close_failure_restores_and_withdraws(monkeypatch, error_kind):
    error = EngineUnhealthy("stalled") if error_kind == "engine" else OSError("failed")
    coordinator = make_coordinator(
        [],
        controller_options={"exit_recording_error": error},
    )
    fatal_errors = []
    ordinary_errors = []
    window = make_settings_window(
        monkeypatch,
        coordinator=coordinator,
        on_engine_unhealthy=fatal_errors.append,
    )
    window._show_error = ordinary_errors.append
    window._record()
    window._on_key_press(FakeTkEvent(0xFF, keysym="Control_L"))

    window._close()

    assert window.hotkey_var.get() == "F24"
    assert window.view.recording is False
    assert window.hotkey_entry.configured["state"] == "readonly"
    assert window.hotkey_entry.bindings == {}
    assert window.window.bindings == {}
    assert window.window.withdrawn is True
    assert window.window.protocols["WM_DELETE_WINDOW"] == window._close
    if error_kind == "engine":
        assert fatal_errors == [error]
        assert ordinary_errors == []
    else:
        assert fatal_errors == []
        assert ordinary_errors == [error]
