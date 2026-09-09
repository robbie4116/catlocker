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
from keyboard_hook import EngineEvent, HookTimeout
from main import AppLifecycle, create_application, main
from settings import AppSettings
from settings_window import StartupUpdateResult
from single_instance import AcquireResult, InstanceError, InstanceRole
from tray import TrayAction, TrayFatalEvent, TrayTimeout


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


class PartiallyStartedHook(FakeHook):
    def start(self, timeout):
        self.wait_timeouts.append(timeout)
        self.calls.append(("hook_start_attempt",))
        raise HookTimeout("hook startup timed out")


class PartiallyStartedStalledHook(StalledHookCleanup):
    def start(self, timeout):
        self.wait_timeouts.append(timeout)
        self.calls.append(("hook_start_attempt",))
        raise HookTimeout("hook startup timed out")


class FakeController:
    def __init__(self, calls, unlock_error=None):
        self.calls = calls
        self.locked = False
        self.events = SimpleQueue()
        self.unlock_error = unlock_error

    def lock(self):
        self.calls.append(("controller_lock", threading.get_ident()))
        self.locked = True

    def unlock(self, *, timeout=None):
        self.calls.append(("controller_unlock", threading.get_ident()))
        self.unlock_timeouts = getattr(self, "unlock_timeouts", [])
        self.unlock_timeouts.append(timeout)
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


class PartiallyStartedTray(FakeTray):
    def start(self, timeout):
        self.wait_timeouts.append(timeout)
        self.calls.append(("tray_start_attempt",))
        raise TrayTimeout("tray startup timed out")


class PartiallyStartedStalledTray(StalledTrayCleanup):
    def start(self, timeout):
        self.wait_timeouts.append(timeout)
        self.calls.append(("tray_start_attempt",))
        raise TrayTimeout("tray startup timed out")


class RuntimeFatalTray(FakeTray):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.events = SimpleQueue()
        self.runtime_error = RuntimeError("tray runtime failed")

    def start(self, timeout):
        super().start(timeout)
        self.events.put(TrayFatalEvent(self.runtime_error))


class FakeSettingsWindow:
    def __init__(self, calls):
        self.calls = calls
        self.view = SimpleNamespace(startup_enabled=False)

    def show(self):
        self.calls.append(("settings_show", threading.get_ident()))

    def on_lock_state(self, locked):
        self.calls.append(("settings_lock_state", bool(locked)))

    def on_startup_state(self, enabled):
        self.view.startup_enabled = bool(enabled)
        self.calls.append(("settings_startup_state", bool(enabled)))


class FakeCoordinator:
    def __init__(self, calls, startup_result=None):
        self.calls = calls
        self.startup_result = startup_result
        self.startup_requests = []

    def set_startup_enabled(self, enabled):
        self.calls.append(("startup_toggle", threading.get_ident()))
        self.startup_requests.append(bool(enabled))
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
        self.startup_command = None

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
        self.startup_command = command
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

    def create_settings_window(
        self,
        root,
        coordinator,
        startup_enabled,
        on_engine_unhealthy=None,
        on_startup_result=None,
    ):
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
    activation_source=None,
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
    if activation_source is not None:
        lifecycle_options["activation_source"] = activation_source
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


def test_hook_start_timeout_stops_partially_started_hook():
    hook = PartiallyStartedHook([], SimpleNamespace(canonical="F24"))
    app, calls = make_app(hook=hook)

    with pytest.raises(HookTimeout, match="hook startup timed out"):
        app.start()

    assert ("hook_stop",) in hook.calls
    assert ("tray_start",) not in calls


def test_tray_start_timeout_stops_partially_started_tray():
    tray = PartiallyStartedTray([])
    app, calls = make_app(tray=tray)

    with pytest.raises(TrayTimeout, match="tray startup timed out"):
        app.start()

    assert ("tray_stop",) in tray.calls
    assert ("hook_stop",) in calls


@pytest.mark.parametrize("component", ["hook", "tray"])
def test_startup_failure_cleanup_is_bounded_for_stalled_workers(component):
    if component == "hook":
        worker = PartiallyStartedStalledHook(
            [],
            SimpleNamespace(canonical="F24"),
            stop_error=TimeoutError("hook cleanup stalled"),
        )
        app, _ = make_app(hook=worker)
        started = worker.force_started
        release = worker.release_force
    else:
        worker = PartiallyStartedStalledTray(
            [],
            stop_error=TimeoutError("tray cleanup stalled"),
        )
        app, _ = make_app(tray=worker)
        started = worker.force_started
        release = worker.release_force

    finished = threading.Event()

    def start_application():
        try:
            app.start()
        except (HookTimeout, TrayTimeout):
            pass
        finally:
            finished.set()

    thread = threading.Thread(target=start_application)
    thread.start()
    try:
        assert started.wait(timeout=1)
        assert finished.wait(timeout=app.shutdown_timeout + 0.25)
        assert ("root_destroy",) in app.root.calls
    finally:
        release.set()
        thread.join(timeout=1)


def test_shutdown_unlock_receives_remaining_global_budget():
    app, _ = make_app(shutdown_timeout=0.01)
    app.start()
    app.shutdown()

    assert app.controller.unlock_timeouts
    assert 0 <= app.controller.unlock_timeouts[0] <= app.shutdown_timeout


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


def test_settings_startup_change_updates_lifecycle_tray_and_view():
    app, _ = make_app(startup_result=StartupUpdateResult(True))
    app.start()
    app.apply_startup_result(app.coordinator.set_startup_enabled(True))

    assert app._startup_enabled is True
    assert app.tray.state.startup_enabled is True
    assert app.settings_window.view.startup_enabled is True

    app.coordinator.startup_result = StartupUpdateResult(False)
    app.handle_tray_action(TrayAction.STARTUP)
    assert app.coordinator.startup_requests[-1] is False


def test_settings_startup_failure_publishes_actual_state_everywhere():
    error = PermissionError("registry denied")
    app, calls = make_app()
    app.start()
    app.apply_startup_result(StartupUpdateResult(True, error))

    assert app._startup_enabled is True
    assert app.tray.state.startup_enabled is True
    assert app.settings_window.view.startup_enabled is True
    assert ("root_show_error", "registry denied") in calls


def test_settings_startup_failure_with_unknown_state_preserves_presentations():
    error = PermissionError("registry unavailable")
    app, calls = make_app()
    app.start()
    app.apply_startup_result(StartupUpdateResult(True))
    calls.clear()

    app.apply_startup_result(StartupUpdateResult(None, error))

    assert app._startup_enabled is True
    assert app.tray.state.startup_enabled is True
    assert app.settings_window.view.startup_enabled is True
    assert calls == [("root_show_error", "registry unavailable")]


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


def test_create_application_installs_toggle_pair_at_startup_despite_differing_stored_pair(
    tmp_path,
):
    """Single mode must win at startup even when a stale, differing lock/unlock pair
    is remembered from a previous separate-mode session."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'separate_shortcuts = false\n'
        'toggle_hotkey = "Ctrl+Alt+F12"\n'
        'lock_hotkey = "F13"\n'
        'unlock_hotkey = "F14"\n'
        'notifications = false\n',
        encoding="utf-8",
    )
    factories = RecordingFactories(startup_enabled=True)
    create_application(
        config_path=config_path,
        executable=tmp_path / "CatLocker.exe",
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.hook_shortcut.lock.canonical == "Ctrl+Alt+F12"
    assert factories.hook_shortcut.unlock.canonical == "Ctrl+Alt+F12"


def test_create_application_installs_lock_unlock_pair_at_startup_in_separate_mode(
    tmp_path,
):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'separate_shortcuts = true\n'
        'toggle_hotkey = "F15"\n'
        'lock_hotkey = "F13"\n'
        'unlock_hotkey = "F14"\n'
        'notifications = false\n',
        encoding="utf-8",
    )
    factories = RecordingFactories(startup_enabled=True)
    create_application(
        config_path=config_path,
        executable=tmp_path / "CatLocker.exe",
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.hook_shortcut.lock.canonical == "F13"
    assert factories.hook_shortcut.unlock.canonical == "F14"


def test_frozen_mode_builds_executable_only_startup_command(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    factories = RecordingFactories()
    create_application(
        config_path=tmp_path / "config.toml",
        executable=Path(r"C:\Program Files\CatLocker\CatLocker.exe"),
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.startup_command == (
        '"C:\\Program Files\\CatLocker\\CatLocker.exe" --startup'
    )


def test_source_mode_prefers_sibling_pythonw_for_startup(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    python_dir = tmp_path / "Python"
    python_dir.mkdir()
    python = python_dir / "python.exe"
    pythonw = python_dir / "pythonw.exe"
    python.write_bytes(b"")
    pythonw.write_bytes(b"")
    factories = RecordingFactories()
    create_application(
        config_path=tmp_path / "config.toml",
        executable=python,
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.startup_command == (
        f'"{pythonw}" "{(ROOT / "main.py").resolve()}" --startup'
    )


def test_source_mode_falls_back_to_resolved_sys_executable(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    factories = RecordingFactories()
    executable = Path(r"C:\Portable Python\python.exe")
    create_application(
        config_path=tmp_path / "config.toml",
        executable=executable,
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.startup_command == (
        f'"{executable}" "{(ROOT / "main.py").resolve()}" --startup'
    )


def test_exe_suffix_does_not_select_frozen_startup_mode(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    factories = RecordingFactories()
    executable = tmp_path / "CatLocker.exe"
    create_application(
        config_path=tmp_path / "config.toml",
        executable=executable,
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.startup_command == (
        f'"{executable.resolve()}" "{(ROOT / "main.py").resolve()}" --startup'
    )


def test_source_mode_uses_existing_source_adjacent_portable_config(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delattr(sys, "frozen", raising=False)
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_main = source_root / "main.py"
    source_main.write_text("", encoding="utf-8")
    (source_root / "catlocker.toml").write_text(
        'toggle_hotkey = "K"\nnotifications = false\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules["main"], "__file__", str(source_main))
    factories = RecordingFactories()
    create_application(
        executable=tmp_path / "python" / "python.exe",
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.coordinator_settings == AppSettings("K", False)


def test_source_mode_without_portable_config_uses_local_appdata(
    monkeypatch,
    tmp_path,
):
    monkeypatch.delattr(sys, "frozen", raising=False)
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_main = source_root / "main.py"
    source_main.write_text("", encoding="utf-8")
    local_appdata = tmp_path / "local"
    monkeypatch.setattr(sys.modules["main"], "__file__", str(source_main))
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    factories = RecordingFactories()
    create_application(
        executable=tmp_path / "python" / "python.exe",
        resource_root=tmp_path,
        factories=factories,
    )

    assert factories.coordinator_settings == AppSettings()
    assert (local_appdata / "CatLocker" / "config.toml").exists()


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


def test_lifecycle_shuts_down_when_tray_runtime_fails():
    tray = RuntimeFatalTray([])
    app, calls = make_app(tray=tray)
    app.start()
    app.pump_events()

    assert app.closing is True
    assert app.running is False
    assert ("controller_fail_open",) in calls
    assert ("hook_stop",) in calls
    assert ("root_destroy",) in calls
    assert ("root_show_error", "tray runtime failed") in calls


# --- Activation (single-instance, Task 4) -----------------------------------
#
# `AppLifecycle` can be given an `activation_source` -- a zero-arg callable
# (in production, `InstanceGuard.poll_activation`) polled once per pump
# iteration. A signal enqueues a typed `ActivateExisting` action onto the
# same `actions` queue tray actions already flow through, so it is handled
# on the Tk main thread through the ordinary action-draining loop.

_ACTIVATION_LOCKED_MESSAGE = "CatLocker is already running; the keyboard is locked."


def test_signaled_activation_queues_activate_existing_and_dispatches_on_main_thread():
    from main import ActivateExisting

    app, calls = make_app(activation_source=lambda: True)
    app.start()
    seen = []
    original = app.handle_tray_action

    def spy(action):
        seen.append((action, threading.get_ident()))
        original(action)

    app.handle_tray_action = spy

    app.pump_events()

    assert len(seen) == 1
    action, thread_id = seen[0]
    assert isinstance(action, ActivateExisting)
    assert thread_id == threading.get_ident()
    assert ("settings_show", threading.get_ident()) in calls


def test_activation_not_polled_or_queued_when_no_source_configured():
    app, calls = make_app()
    app.start()
    app.pump_events()
    assert not any(call[0] == "settings_show" for call in calls)


def test_activation_not_queued_when_poll_returns_false():
    app, calls = make_app(activation_source=lambda: False)
    app.start()
    app.pump_events()
    assert not any(call[0] == "settings_show" for call in calls)


def test_handle_tray_action_rejects_activation_off_main_thread():
    from main import ActivateExisting

    app, calls = make_app()
    app.start()
    errors = []

    def call_off_thread():
        try:
            app.handle_tray_action(ActivateExisting())
        except RuntimeError as exc:
            errors.append(exc)

    thread = threading.Thread(target=call_off_thread)
    thread.start()
    thread.join(timeout=1)

    assert len(errors) == 1
    assert "main thread" in str(errors[0]).lower()
    assert not any(call[0] == "settings_show" for call in calls)


def test_activation_rechecks_lock_state_at_handling_time_not_signaling_time():
    """The lock state must be read fresh inside `_handle_activation`, not
    captured back when the signal was first observed. Flip the controller to
    locked from inside the activation source itself -- unlocked "at signal
    time", locked by the time the queued action is actually handled."""

    app, calls = make_app()
    app.start()

    def activation_source():
        app.controller.locked = True
        return True

    app._activation_source = activation_source
    app._show_locked_message = lambda text: calls.append(("locked_message", text))

    app.pump_events()

    assert not any(call[0] == "settings_show" for call in calls)
    assert ("locked_message", _ACTIVATION_LOCKED_MESSAGE) in calls


def test_unlocked_activation_shows_existing_settings_window():
    app, calls = make_app()
    app.start()
    app._handle_activation()
    assert ("settings_show", threading.get_ident()) in calls


def test_locked_activation_shows_message_and_never_touches_settings():
    app, calls = make_app()
    app.start()
    app.controller.locked = True
    calls.clear()
    shown = []
    app._show_locked_message = lambda text: shown.append(text)

    app._handle_activation()

    assert shown == [_ACTIVATION_LOCKED_MESSAGE]
    assert not any(call[0] == "settings_show" for call in calls)


@pytest.mark.parametrize("locked", [False, True])
def test_activation_never_invokes_lock_unlock_or_toggle(locked):
    app, calls = make_app()
    app.start()
    app.controller.locked = locked
    app._show_locked_message = lambda text: None
    calls.clear()

    app._handle_activation()

    assert not any(
        call[0] in ("controller_lock", "controller_unlock", "controller_toggle")
        for call in calls
    )


def test_repeated_activation_while_dialog_open_is_coalesced():
    app, calls = make_app()
    app.start()
    app.controller.locked = True
    dialog_calls = []

    def fake_show_locked_message(text):
        dialog_calls.append(text)
        # Simulate Tk's nested modal event loop still pumping `after()`
        # callbacks while `messagebox.showinfo` is blocking -- a second
        # activation signal arriving mid-dialog must not open another one.
        app._handle_activation()

    app._show_locked_message = fake_show_locked_message

    app._handle_activation()

    assert dialog_calls == [_ACTIVATION_LOCKED_MESSAGE]
    assert app._activation_dialog_open is False


def test_show_locked_message_default_calls_tkinter_messagebox_showinfo(monkeypatch):
    from types import ModuleType

    recorded = []
    fake_tk = ModuleType("tkinter")
    fake_messagebox = ModuleType("tkinter.messagebox")

    def fake_showinfo(title, message, parent=None):
        recorded.append((title, message, parent))

    fake_messagebox.showinfo = fake_showinfo
    fake_tk.messagebox = fake_messagebox
    monkeypatch.setitem(sys.modules, "tkinter", fake_tk)
    monkeypatch.setitem(sys.modules, "tkinter.messagebox", fake_messagebox)

    app, _ = make_app()
    app._show_locked_message(_ACTIVATION_LOCKED_MESSAGE)

    assert recorded == [("CatLocker", _ACTIVATION_LOCKED_MESSAGE, app.root)]


def test_locked_activation_message_independent_of_notifications_preference():
    app, calls = make_app()
    app.start()
    app.controller.locked = True
    # Even a coordinator reporting notifications disabled must not suppress
    # or alter the locked-activation dialog: it is never gated on the
    # tray/settings notification preference.
    app.coordinator.current = SimpleNamespace(notifications=False)
    shown = []
    app._show_locked_message = lambda text: shown.append(text)

    app._handle_activation()

    assert shown == [_ACTIVATION_LOCKED_MESSAGE]


def test_pump_events_ignores_activation_while_closing():
    activation_calls = []

    def activation_source():
        activation_calls.append(1)
        return True

    app, calls = make_app(activation_source=activation_source)
    app.start()
    app.shutdown()
    calls.clear()

    app.pump_events()

    assert activation_calls == []
    assert not any(call[0] == "settings_show" for call in calls)


def test_activation_poll_failure_reported_once_and_disables_further_polling():
    from single_instance import InstanceError

    poll_calls = []

    def failing_poll():
        poll_calls.append(1)
        raise InstanceError("WaitForSingleObject failed while polling.")

    app, calls = make_app(activation_source=failing_poll)
    app.start()
    calls.clear()

    app.pump_events()

    assert len(poll_calls) == 1
    assert any(
        call == ("root_show_error", "WaitForSingleObject failed while polling.")
        for call in calls
    )
    assert app._activation_source is None
    assert app.running is True
    assert app.closing is False
    assert not any(
        call[0] in ("hook_stop", "tray_stop", "controller_fail_open")
        for call in calls
    )

    # Polling must never be attempted again for the rest of this run, and the
    # app must keep pumping normally (hook/tray/keyboard recovery untouched).
    app.pump_events()
    assert len(poll_calls) == 1


def test_activation_handling_failure_reported_and_pump_not_frozen():
    """`_handle_activation()` calls into Tk (`settings_window.show()` here,
    `messagebox.showinfo()` via `_show_locked_message` for the locked
    branch), which can raise for reasons unrelated to the keyboard engine --
    a `TclError` from a misbehaving root, a theme issue, etc.

    Such a failure must be reported like the activation poll failure above,
    not left to propagate out of `pump_events()`: an escaping exception
    would skip the trailing `self._schedule_pump()` call, and since nothing
    else ever re-arms `root.after()` for the next tick, the entire 25ms pump
    loop would freeze permanently -- tray updates, all tray-menu actions,
    and all future activation requests stop, even though the keyboard hook
    itself (on its own thread) would keep working.
    """

    app, calls = make_app(activation_source=lambda: True)
    app.start()
    calls.clear()
    app.root.after_calls.clear()

    def failing_show():
        raise RuntimeError("settings window failed to show")

    app.settings_window.show = failing_show

    # Drive one real pump tick the way `root.after()` would actually invoke
    # it: `_run_scheduled_pump` clears the "pump scheduled" flag *before*
    # calling `pump_events()`, so if `pump_events()` fails to reach its
    # trailing `_schedule_pump()` call, the flag is left False and no future
    # tick is ever armed again.
    app._run_scheduled_pump()  # must not raise -- see assertion (a) below

    # (a) The exception did not propagate out of pump_events(): the call
    # above already returned normally instead of raising, which is itself
    # the primary regression check.

    # (b) The exception was reported exactly once via `_report_error`.
    assert calls.count(("root_show_error", "settings window failed to show")) == 1

    # (c) The pump loop is still armed for the next tick -- proof the loop
    # did not freeze.
    assert app._pump_scheduled is True
    assert len(app.root.after_calls) == 1

    # The activation dialog bookkeeping must not be left stuck either.
    assert app._activation_dialog_open is False


def test_shutdown_clears_activation_source():
    app, _ = make_app(activation_source=lambda: True)
    app.start()
    app.shutdown()
    assert app._activation_source is None


# --- Guarded entry point (single-instance) ---------------------------------
#
# `main()` wraps the composition root (`create_application()`) with a
# single-instance guard (`single_instance.InstanceGuard`). These tests never
# touch a real OS mutex/event or a real Win32 MessageBox -- they inject a
# fake guard and a spy/fake application factory and message-box sink.


class FakeGuard:
    """Stand-in for `single_instance.InstanceGuard` used only by `main()`."""

    def __init__(
        self,
        *,
        role=InstanceRole.OWNER,
        acquire_error=None,
        activation_result=True,
    ):
        self.calls = []
        self.role = role
        self.acquire_error = acquire_error
        self.activation_result = activation_result
        self.closed = False

    def acquire(self):
        self.calls.append("acquire")
        if self.acquire_error is not None:
            raise self.acquire_error
        return AcquireResult(self.role)

    def request_activation(self, timeout=2.0):
        self.calls.append(("request_activation", timeout))
        return self.activation_result

    def poll_activation(self):
        self.calls.append("poll_activation")
        return False

    def close(self):
        self.calls.append("close")
        self.closed = True


class RecordingMessageBox:
    def __init__(self):
        self.calls = []

    def __call__(self, title, text):
        self.calls.append((title, text))


def _never_call_application_factory():
    raise AssertionError("application_factory must not be called for a duplicate instance")


def test_main_owner_path_acquires_ownership_before_building_and_running_app():
    """Ownership must be established before ANY application construction,
    and both construction and run() must be wrapped so the guard is always
    released, even on the successful path."""

    events = []
    guard = FakeGuard(role=InstanceRole.OWNER)

    def spy_acquire():
        events.append("acquire")
        return AcquireResult(InstanceRole.OWNER)

    def spy_close():
        events.append("close")

    guard.acquire = spy_acquire
    guard.close = spy_close

    fake_app = SimpleNamespace(run=lambda: events.append("run"))

    def application_factory(activation_source=None):
        events.append("application_factory")
        return fake_app

    exit_code = main(
        [],
        guard_factory=lambda: guard,
        application_factory=application_factory,
        message_box=RecordingMessageBox(),
    )

    assert exit_code == 0
    assert events == ["acquire", "application_factory", "run", "close"]


def test_main_owner_path_passes_guard_poll_activation_as_activation_source():
    """The owner's activation source must be the guard's own bound
    `poll_activation` method, threaded through `application_factory` -- not
    reimplemented, wrapped, or omitted."""

    guard = FakeGuard(role=InstanceRole.OWNER)
    received = {}

    def application_factory(*, activation_source=None):
        received["activation_source"] = activation_source
        return SimpleNamespace(run=lambda: None)

    main(
        [],
        guard_factory=lambda: guard,
        application_factory=application_factory,
        message_box=RecordingMessageBox(),
    )

    assert received["activation_source"] == guard.poll_activation


@pytest.mark.parametrize("startup_flag", [[], ["--startup"]])
def test_main_duplicate_path_never_calls_application_factory(startup_flag):
    guard = FakeGuard(role=InstanceRole.DUPLICATE, activation_result=False)

    main(
        startup_flag,
        guard_factory=lambda: guard,
        application_factory=_never_call_application_factory,
        message_box=RecordingMessageBox(),
    )


def test_main_normal_duplicate_launch_requests_activation_and_succeeds_quietly():
    guard = FakeGuard(role=InstanceRole.DUPLICATE, activation_result=True)
    message_box = RecordingMessageBox()

    exit_code = main(
        [],
        guard_factory=lambda: guard,
        application_factory=_never_call_application_factory,
        message_box=message_box,
    )

    assert exit_code == 0
    assert ("request_activation", 2.0) in guard.calls
    assert message_box.calls == []
    assert guard.closed is True


def test_main_duplicate_launch_activation_failure_shows_tray_message():
    guard = FakeGuard(role=InstanceRole.DUPLICATE, activation_result=False)
    message_box = RecordingMessageBox()

    exit_code = main(
        [],
        guard_factory=lambda: guard,
        application_factory=_never_call_application_factory,
        message_box=message_box,
    )

    assert exit_code != 0
    assert ("request_activation", 2.0) in guard.calls
    assert len(message_box.calls) == 1
    title, text = message_box.calls[0]
    assert "tray" in text.lower()
    assert guard.closed is True


def test_main_duplicate_startup_launch_exits_quietly_without_activation_or_dialog():
    guard = FakeGuard(role=InstanceRole.DUPLICATE, activation_result=True)
    message_box = RecordingMessageBox()

    exit_code = main(
        ["--startup"],
        guard_factory=lambda: guard,
        application_factory=_never_call_application_factory,
        message_box=message_box,
    )

    assert exit_code == 0
    assert not any(call[0] == "request_activation" for call in guard.calls)
    assert message_box.calls == []
    assert guard.closed is True


def test_main_owner_construction_failure_still_closes_guard_and_propagates():
    guard = FakeGuard(role=InstanceRole.OWNER)
    error = RuntimeError("failed to build application")

    def application_factory(activation_source=None):
        raise error

    with pytest.raises(RuntimeError, match="failed to build application"):
        main(
            [],
            guard_factory=lambda: guard,
            application_factory=application_factory,
            message_box=RecordingMessageBox(),
        )

    assert guard.closed is True


def test_main_owner_run_failure_still_closes_guard_and_propagates():
    guard = FakeGuard(role=InstanceRole.OWNER)
    error = RuntimeError("run failed")

    def failing_run():
        raise error

    application_factory = lambda activation_source=None: SimpleNamespace(run=failing_run)

    with pytest.raises(RuntimeError, match="run failed"):
        main(
            [],
            guard_factory=lambda: guard,
            application_factory=application_factory,
            message_box=RecordingMessageBox(),
        )

    assert guard.closed is True


def test_main_ownership_error_reports_and_exits_without_constructing_app():
    guard = FakeGuard(acquire_error=InstanceError("mutex creation failed"))
    message_box = RecordingMessageBox()

    exit_code = main(
        [],
        guard_factory=lambda: guard,
        application_factory=_never_call_application_factory,
        message_box=message_box,
    )

    assert exit_code != 0
    assert len(message_box.calls) == 1
    assert "mutex creation failed" in message_box.calls[0][1]
    assert guard.closed is True


def test_main_unexpected_acquire_failure_still_closes_guard_and_propagates():
    """A non-InstanceError bug inside acquire() must not skip cleanup: only
    InstanceError is treated as the documented "unexpected OS error" case
    that gets reported via message_box, but any other exception should still
    release the guard before propagating."""

    guard = FakeGuard(acquire_error=RuntimeError("adapter bug"))
    message_box = RecordingMessageBox()

    with pytest.raises(RuntimeError, match="adapter bug"):
        main(
            [],
            guard_factory=lambda: guard,
            application_factory=_never_call_application_factory,
            message_box=message_box,
        )

    assert guard.closed is True
    assert message_box.calls == []
