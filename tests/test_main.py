from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_importing_main_has_no_gui_or_mainloop_side_effects():
    completed = subprocess.run(
        [sys.executable, "-c", "import main; print('imported')"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )
    assert completed.stdout.strip() == "imported"


import threading
import math
from queue import SimpleQueue
from types import SimpleNamespace

import pytest

from controller import EngineUnhealthy
from keyboard_hook import EngineEvent
from main import AppLifecycle, create_application
from settings import AppSettings
from settings_window import StartupUpdateResult
from tray import TrayAction


class FakeRoot:
    def __init__(self, calls):
        self.calls = calls
        self.withdrawn = False
        self.after_calls = []

    def withdraw(self):
        self.withdrawn = True
        self.calls.append(("root_withdraw",))

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return len(self.after_calls)

    def mainloop(self):
        self.calls.append(("root_mainloop",))

    def destroy(self):
        self.calls.append(("root_destroy",))

    def show_error(self, error):
        self.calls.append(("root_show_error", str(error)))


class FakeHook:
    def __init__(self, calls, shortcut, start_error=None, stop_error=None):
        self.calls = calls
        self.shortcut = shortcut
        self.events = SimpleQueue()
        self.locked = False
        self.start_error = start_error
        self.stop_error = stop_error
        self.wait_timeouts = []
        self.stop_timeouts = []

    def start(self, timeout):
        assert math.isfinite(float(timeout))
        self.wait_timeouts.append(timeout)
        if self.start_error is not None:
            raise self.start_error
        self.calls.append(("hook_start",))

    def stop(self, timeout):
        assert math.isfinite(float(timeout))
        self.wait_timeouts.append(timeout)
        self.stop_timeouts.append(timeout)
        self.calls.append(("hook_stop",))
        if self.stop_error is not None:
            raise self.stop_error

    def enter_fail_open(self):
        self.calls.append(("hook_enter_fail_open",))

    def force_unhook(self):
        self.calls.append(("hook_force_unhook",))

    def post_quit(self):
        self.calls.append(("hook_post_quit",))


class StalledHookCleanup(FakeHook):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.force_started = threading.Event()
        self.release_force = threading.Event()

    def force_unhook(self):
        self.calls.append(("hook_force_unhook",))
        self.force_started.set()
        self.release_force.wait(timeout=1)


class FakeController:
    def __init__(self, calls, unlock_error=None):
        self.calls = calls
        self.locked = False
        self.events = SimpleQueue()
        self.unlock_error = unlock_error

    def lock(self):
        self.calls.append(("controller_lock", threading.get_ident()))
        self.locked = True

    def unlock(self):
        self.calls.append(("controller_unlock", threading.get_ident()))
        if self.unlock_error is not None:
            raise self.unlock_error
        self.locked = False

    def toggle(self):
        self.calls.append(("controller_toggle", threading.get_ident()))
        self.locked = not self.locked

    def enter_fail_open(self):
        self.calls.append(("controller_fail_open",))
        self.locked = False


class FakeTray:
    def __init__(self, calls, startup_enabled=False, start_error=None, stop_error=None):
        self.calls = calls
        self.actions = SimpleQueue()
        self.state = SimpleNamespace(startup_enabled=startup_enabled)
        self.start_error = start_error
        self.stop_error = stop_error
        self.wait_timeouts = []
        self.stop_timeouts = []

    def start(self, timeout):
        assert math.isfinite(float(timeout))
        self.wait_timeouts.append(timeout)
        if self.start_error is not None:
            raise self.start_error
        self.calls.append(("tray_start",))

    def stop(self, timeout):
        assert math.isfinite(float(timeout))
        self.wait_timeouts.append(timeout)
        self.stop_timeouts.append(timeout)
        self.calls.append(("tray_stop",))
        if self.stop_error is not None:
            raise self.stop_error

    def post_update(self, update):
        if update.startup_enabled is not None:
            self.state.startup_enabled = update.startup_enabled
        self.calls.append(("tray_update", update))

    def post_startup_state(self, enabled):
        self.state.startup_enabled = bool(enabled)
        self.calls.append(("tray_startup_state", bool(enabled)))

    def post_notifications_enabled(self, enabled):
        self.calls.append(("tray_notifications", bool(enabled)))

    def force_remove_icon(self):
        self.calls.append(("tray_force_remove_icon",))

    def post_quit(self):
        self.calls.append(("tray_post_quit",))


class StalledTrayCleanup(FakeTray):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.force_started = threading.Event()
        self.release_force = threading.Event()

    def force_remove_icon(self):
        self.calls.append(("tray_force_remove_icon",))
        self.force_started.set()
        self.release_force.wait(timeout=1)


class FakeSettingsWindow:
    def __init__(self, calls):
        self.calls = calls

    def show(self):
        self.calls.append(("settings_show", threading.get_ident()))

    def on_lock_state(self, locked):
        self.calls.append(("settings_lock_state", bool(locked)))

    def on_startup_state(self, enabled):
        self.calls.append(("settings_startup_state", bool(enabled)))


class FakeCoordinator:
    def __init__(self, calls, startup_result=None):
        self.calls = calls
        self.startup_result = startup_result

    def set_startup_enabled(self, enabled):
        self.calls.append(("startup_toggle", threading.get_ident()))
        if self.startup_result is not None:
            return self.startup_result
        return StartupUpdateResult(bool(enabled))


class RecordingFactories:
    def __init__(self, startup_enabled=False):
        self.calls = []
        self.root = None
        self.startup_enabled = startup_enabled
        self.hook_shortcut = None
        self.tray_notifications = None
        self.tray_startup_enabled = None
        self.coordinator_settings = None

    def create_root(self):
        self.root = FakeRoot(self.calls)
        return self.root

    def create_hook(self, shortcut):
        self.root_withdrawn_before_components = self.root.withdrawn
        self.hook_shortcut = shortcut
        return FakeHook(self.calls, shortcut)

    def create_controller(self, hook):
        return FakeController(self.calls)

    def create_startup_registry(self, command):
        return SimpleNamespace(is_enabled=lambda: self.startup_enabled)

    def create_tray(self, actions, startup_enabled, notifications, icon_path):
        self.tray_notifications = notifications
        self.tray_startup_enabled = startup_enabled
        return FakeTray(self.calls, startup_enabled=startup_enabled)

    def create_coordinator(
        self,
        current,
        controller,
        persist,
        startup_registry,
        apply_notifications,
    ):
        self.coordinator_settings = current
        return FakeCoordinator(self.calls)

    def create_settings_window(self, root, coordinator, startup_enabled):
        return FakeSettingsWindow(self.calls)

    def show_error(self, root, error):
        root.show_error(error)


def make_app(
    *,
    hook_start_error=None,
    tray_start_error=None,
    startup_result=None,
    unlock_error=None,
    hook_stop_error=None,
    tray_stop_error=None,
    hook=None,
    tray=None,
    shutdown_timeout=None,
):
    calls = []
    root = FakeRoot(calls)
    hook = hook or FakeHook(
        calls,
        SimpleNamespace(canonical="F24"),
        hook_start_error,
        hook_stop_error,
    )
    hook.start_error = hook_start_error
    controller = FakeController(calls, unlock_error=unlock_error)
    tray = tray or FakeTray(
        calls,
        start_error=tray_start_error,
        stop_error=tray_stop_error,
    )
    coordinator = FakeCoordinator(calls, startup_result=startup_result)
    settings_window = FakeSettingsWindow(calls)
    lifecycle_options = {}
    if shutdown_timeout is not None:
        lifecycle_options["shutdown_timeout"] = shutdown_timeout
    app = AppLifecycle(
        root=root,
        hook=hook,
        controller=controller,
        tray=tray,
        coordinator=coordinator,
        settings_window=settings_window,
        actions=tray.actions,
        show_error=root.show_error,
        command_timeout=0.25,
        thread_timeout=0.5,
        **lifecycle_options,
    )
    return app, calls


def test_startup_begins_unlocked_and_waits_for_hook_before_tray():
    app, calls = make_app()
    app.start()
    assert calls[:3] == [
        ("root_withdraw",),
        ("hook_start",),
        ("tray_start",),
    ]
    assert app.controller.locked is False


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (TrayAction.LOCK, "controller_lock"),
        (TrayAction.UNLOCK, "controller_unlock"),
        (TrayAction.TOGGLE, "controller_toggle"),
        (TrayAction.SETTINGS, "settings_show"),
        (TrayAction.STARTUP, "startup_toggle"),
    ],
)
def test_tray_actions_execute_on_main_thread(action, expected):
    app, calls = make_app()
    app.start()
    app.handle_tray_action(action)
    assert (expected, threading.get_ident()) in calls


def test_event_pump_forwards_authoritative_state_and_queued_actions():
    app, calls = make_app()
    app.start()
    app.controller.events.put(EngineEvent("state", True, reason="toggle"))
    app.actions.put(TrayAction.UNLOCK)

    app.pump_events()

    updates = [call[1] for call in calls if call[0] == "tray_update"]
    assert updates and updates[0].locked is True
    assert ("settings_lock_state", True) in calls
    assert ("controller_unlock", threading.get_ident()) in calls


def test_hook_install_failure_never_starts_tray_and_reports_error():
    app, calls = make_app(hook_start_error=OSError("hook failed"))
    with pytest.raises(OSError, match="hook failed"):
        app.start()
    assert ("tray_start",) not in calls
    assert ("root_show_error", "hook failed") in calls


def test_tray_start_failure_stops_hook_before_reporting():
    app, calls = make_app(tray_start_error=OSError("tray failed"))
    with pytest.raises(OSError, match="tray failed"):
        app.start()
    assert calls.index(("hook_stop",)) < calls.index(("root_show_error", "tray failed"))


def test_startup_action_refreshes_presentations_and_shows_error():
    error = PermissionError("registry denied")
    app, calls = make_app(
        startup_result=StartupUpdateResult(False, error),
    )
    app.start()
    app.handle_tray_action(TrayAction.STARTUP)
    assert ("tray_startup_state", False) in calls
    assert ("settings_startup_state", False) in calls
    assert ("root_show_error", "registry denied") in calls


def test_create_application_wires_loaded_settings_and_actual_startup(tmp_path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'toggle_hotkey = "Ctrl+Alt+F12"\nnotifications = false\n',
        encoding="utf-8",
    )
    factories = RecordingFactories(startup_enabled=True)
    app = create_application(
        config_path=config_path,
        executable=tmp_path / "CatLocker.exe",
        resource_root=tmp_path,
        factories=factories,
    )
    assert factories.root_withdrawn_before_components is True
    assert factories.hook_shortcut.canonical == "Ctrl+Alt+F12"
    assert factories.tray_notifications is False
    assert factories.tray_startup_enabled is True
    assert factories.coordinator_settings == AppSettings("Ctrl+Alt+F12", False)


def test_exit_unlocks_then_stops_hook_then_tray_then_tk():
    app, calls = make_app()
    app.start()
    app.handle_tray_action(TrayAction.LOCK)
    calls.clear()
    app.shutdown()
    ordered = [
        calls.index(("controller_unlock", threading.get_ident())),
        calls.index(("hook_stop",)),
        calls.index(("tray_stop",)),
        calls.index(("root_destroy",)),
    ]
    assert ordered == sorted(ordered)


def test_unlock_timeout_enters_fail_open_before_hook_stop():
    app, calls = make_app(unlock_error=EngineUnhealthy("stalled"))
    app.start()
    app.shutdown()
    assert calls.index(("controller_fail_open",)) < calls.index(("hook_stop",))


def test_hook_stop_timeout_forces_fail_open_unhook_and_quit():
    app, calls = make_app(hook_stop_error=TimeoutError("stalled"))
    app.start()
    app.shutdown()
    assert calls.index(("hook_enter_fail_open",)) < calls.index(("hook_force_unhook",))
    assert calls.index(("hook_force_unhook",)) < calls.index(("hook_post_quit",))
    assert ("root_destroy",) in calls
    assert len(app.hook.stop_timeouts) == 2


def test_tray_stop_timeout_best_effort_removes_icon_and_continues():
    app, calls = make_app(tray_stop_error=TimeoutError("stalled"))
    app.start()
    app.shutdown()
    assert calls.index(("tray_force_remove_icon",)) < calls.index(("root_destroy",))
    assert len(app.tray.stop_timeouts) == 2


def test_shutdown_deadline_survives_stalled_hook_cleanup():
    calls = []
    hook = StalledHookCleanup(
        calls,
        SimpleNamespace(canonical="F24"),
        stop_error=TimeoutError("stalled"),
    )
    app, _ = make_app(hook=hook)
    app.start()
    finished = threading.Event()
    shutdown_thread = threading.Thread(
        target=lambda: (app.shutdown(), finished.set())
    )
    shutdown_thread.start()
    try:
        assert hook.force_started.wait(timeout=1)
        assert finished.wait(timeout=app.shutdown_timeout + 0.25)
        assert ("root_destroy",) in app.root.calls
    finally:
        hook.release_force.set()
        shutdown_thread.join(timeout=1)


def test_shutdown_deadline_survives_stalled_tray_cleanup():
    calls = []
    tray = StalledTrayCleanup(
        calls,
        stop_error=TimeoutError("stalled"),
    )
    app, _ = make_app(tray=tray)
    app.start()
    finished = threading.Event()
    shutdown_thread = threading.Thread(
        target=lambda: (app.shutdown(), finished.set())
    )
    shutdown_thread.start()
    try:
        assert tray.force_started.wait(timeout=1)
        assert finished.wait(timeout=app.shutdown_timeout + 0.25)
        assert ("root_destroy",) in app.root.calls
    finally:
        tray.release_force.set()
        shutdown_thread.join(timeout=1)


def test_shutdown_with_zero_remaining_budget_still_destroys_root():
    calls = []
    hook = StalledHookCleanup(
        calls,
        SimpleNamespace(canonical="F24"),
        stop_error=TimeoutError("stalled"),
    )
    app, _ = make_app(hook=hook, shutdown_timeout=0)
    app.start()
    finished = threading.Event()
    shutdown_thread = threading.Thread(
        target=lambda: (app.shutdown(), finished.set())
    )
    shutdown_thread.start()
    try:
        assert hook.force_started.wait(timeout=1)
        assert finished.wait(timeout=0.25)
        assert ("root_destroy",) in app.root.calls
    finally:
        hook.release_force.set()
        shutdown_thread.join(timeout=1)


def test_shutdown_is_idempotent_after_first_close():
    app, calls = make_app()
    app.start()
    app.shutdown()
    first_calls = calls.copy()
    app.shutdown()
    assert calls == first_calls


def test_exit_action_shuts_down_the_application():
    app, calls = make_app()
    app.start()
    app.handle_tray_action(TrayAction.EXIT)
    assert app.closing is True
    assert ("root_destroy",) in calls


def test_fatal_engine_event_fails_open_reports_and_shuts_down():
    app, calls = make_app()
    app.start()
    app.controller.events.put(
        EngineEvent(
            "fatal",
            False,
            reason="callback",
            error=RuntimeError("callback failed"),
        )
    )
    app.pump_events()
    assert any(call[0] == "tray_update" for call in calls)
    assert ("settings_lock_state", False) in calls
    assert ("controller_fail_open",) in calls
    assert ("root_show_error", "callback failed") in calls
    assert ("root_destroy",) in calls
