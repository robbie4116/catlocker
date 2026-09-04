from __future__ import annotations

import math
import os
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from controller import EngineUnhealthy


PUMP_INTERVAL_MS = 25
DEFAULT_COMMAND_TIMEOUT = 1.0
DEFAULT_THREAD_TIMEOUT = 1.0


def resource_path(relative: str | os.PathLike[str]) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _finite_timeout(value: float, operation: str) -> float:
    try:
        timeout = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{operation} timeout must be finite and non-negative.") from exc
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(f"{operation} timeout must be finite and non-negative.")
    return timeout


@dataclass
class ApplicationFactories:
    """Optional construction seams for the application composition root."""

    root: Callable[[], object] | None = None
    hook: Callable[..., object] | None = None
    controller: Callable[..., object] | None = None
    startup_registry: Callable[..., object] | None = None
    tray: Callable[..., object] | None = None
    coordinator: Callable[..., object] | None = None
    settings_window: Callable[..., object] | None = None
    show_error: Callable[..., object] | None = None


def _factory(bundle: object, name: str, default: Callable) -> Callable:
    candidate = getattr(bundle, name, None)
    if callable(candidate):
        return candidate
    candidate = getattr(bundle, f"{name}_factory", None)
    if callable(candidate):
        return candidate
    candidate = getattr(bundle, f"create_{name}", None)
    if callable(candidate):
        return candidate
    return default


class AppLifecycle:
    """Own the main-thread lifetime of CatLocker and its worker owners."""

    def __init__(
        self,
        *,
        root: object,
        hook: object,
        controller: object,
        tray: object,
        coordinator: object,
        settings_window: object,
        actions: queue.SimpleQueue,
        show_error: Callable[[BaseException], object],
        command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
        thread_timeout: float = DEFAULT_THREAD_TIMEOUT,
        shutdown_timeout: float | None = None,
        pump_interval_ms: int = PUMP_INTERVAL_MS,
        startup_registry: object | None = None,
    ) -> None:
        self.root = root
        self.hook = hook
        self.controller = controller
        self.tray = tray
        self.coordinator = coordinator
        self.settings_window = settings_window
        self.actions = actions
        self._show_error = show_error
        self.command_timeout = _finite_timeout(command_timeout, "command")
        self.thread_timeout = _finite_timeout(thread_timeout, "thread")
        configured_shutdown_timeout = (
            self.command_timeout + (4 * self.thread_timeout)
            if shutdown_timeout is None
            else shutdown_timeout
        )
        self.shutdown_timeout = _finite_timeout(
            configured_shutdown_timeout,
            "shutdown",
        )
        self.pump_interval_ms = int(pump_interval_ms)
        self.startup_registry = startup_registry
        self._main_thread_id = threading.get_ident()
        self._running = False
        self._closing = False
        self._fatal_handled = False
        self._root_destroyed = False
        self._pump_scheduled = False
        state = getattr(tray, "state", None)
        self._startup_enabled = bool(getattr(state, "startup_enabled", False))

    @property
    def running(self) -> bool:
        return self._running

    @property
    def closing(self) -> bool:
        return self._closing

    def start(self) -> None:
        """Withdraw the root, then start the hook and tray owners in order."""
        if self._closing:
            return

        self.root.withdraw()
        hook_attempted = False
        tray_attempted = False
        try:
            hook_attempted = True
            self.hook.start(timeout=self.thread_timeout)
            if bool(getattr(self.hook, "locked", False)):
                raise RuntimeError("CatLocker keyboard hook did not start unlocked.")
            tray_attempted = True
            self.tray.start(timeout=self.thread_timeout)
            self._running = True
            self._schedule_pump()
        except BaseException as error:
            self._running = False
            if tray_attempted:
                try:
                    self.tray.stop(timeout=self.thread_timeout)
                except BaseException:
                    pass
            if hook_attempted:
                try:
                    self.hook.stop(timeout=self.thread_timeout)
                except BaseException:
                    pass
            self._report_error(error)
            self._destroy_root()
            raise

    def run(self) -> None:
        self.start()
        try:
            self.root.mainloop()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Fail open first, then perform bounded best-effort cleanup."""
        if self._closing:
            return
        deadline = time.monotonic() + self.shutdown_timeout
        self._closing = True
        self._running = False
        self._pump_scheduled = False

        try:
            self.controller.unlock(
                timeout=self._remaining(deadline, self.command_timeout),
            )
        except BaseException as error:
            self._report_error(error)
        finally:
            self._enter_fail_open()

        self._stop_hook(deadline)
        self._stop_tray(deadline)
        self._destroy_root()

    @staticmethod
    def _remaining(deadline: float, cap: float) -> float:
        return min(cap, max(0.0, deadline - time.monotonic()))

    def _emergency_call(
        self,
        operation: Callable[[], object],
        deadline: float,
    ) -> None:
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                operation()
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(
            target=invoke,
            name="CatLockerEmergencyCleanup",
            daemon=True,
        )
        worker.start()
        worker.join(self._remaining(deadline, self.thread_timeout))
        if errors:
            self._report_error(errors[0])

    def _stop_hook(self, deadline: float) -> None:
        try:
            self.hook.stop(timeout=self._remaining(deadline, self.thread_timeout))
            return
        except BaseException as error:
            self._report_error(error)

        try:
            self.hook.enter_fail_open()
        except BaseException as error:
            self._report_error(error)
        self._emergency_call(self.hook.force_unhook, deadline)
        self._emergency_call(self.hook.post_quit, deadline)
        try:
            self.hook.stop(timeout=self._remaining(deadline, self.thread_timeout))
        except BaseException as error:
            self._report_error(error)

    def _stop_tray(self, deadline: float) -> None:
        try:
            self.tray.stop(timeout=self._remaining(deadline, self.thread_timeout))
            return
        except BaseException as error:
            self._report_error(error)

        self._emergency_call(self.tray.force_remove_icon, deadline)
        self._emergency_call(self.tray.post_quit, deadline)
        try:
            self.tray.stop(timeout=self._remaining(deadline, self.thread_timeout))
        except BaseException as error:
            self._report_error(error)

    def handle_tray_action(self, action) -> None:
        if threading.get_ident() != self._main_thread_id:
            raise RuntimeError("Tray actions must be handled on the Tk main thread.")
        if self._closing:
            return

        try:
            from tray import TrayAction

            if action == TrayAction.LOCK:
                self.controller.lock()
            elif action == TrayAction.UNLOCK:
                self.controller.unlock()
            elif action == TrayAction.TOGGLE:
                self.controller.toggle()
            elif action == TrayAction.SETTINGS:
                self.settings_window.show()
            elif action == TrayAction.STARTUP:
                self._handle_startup_action()
            elif action == TrayAction.EXIT:
                self.shutdown()
        except Exception as error:
            from controller import EngineUnhealthy

            if not isinstance(error, EngineUnhealthy):
                raise
            self._handle_fatal(error)

    def pump_events(self) -> None:
        if not self._running or self._closing:
            return

        from tray import TrayStopped, TrayUpdate

        tray_events = getattr(self.tray, "events", None)
        if tray_events is not None:
            while True:
                try:
                    tray_event = tray_events.get_nowait()
                except queue.Empty:
                    break
                self._handle_fatal(tray_event.error)
                return

        events = getattr(self.controller, "events", self.hook.events)
        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break
            try:
                self.tray.post_update(
                    TrayUpdate(
                        locked=bool(event.locked),
                        reason=event.reason,
                    )
                )
                self.settings_window.on_lock_state(bool(event.locked))
            except (TrayStopped, EngineUnhealthy) as error:
                self._handle_fatal(error)
                return
            if event.kind == "fatal":
                error = event.error or RuntimeError(
                    event.reason or "Keyboard engine failed."
                )
                self._handle_fatal(error)
                return

        while True:
            try:
                action = self.actions.get_nowait()
            except queue.Empty:
                break
            self.handle_tray_action(action)
            if self._closing:
                return

        self._schedule_pump()

    def _schedule_pump(self) -> None:
        if self._running and not self._closing and not self._pump_scheduled:
            self._pump_scheduled = True
            self.root.after(self.pump_interval_ms, self._run_scheduled_pump)

    def _run_scheduled_pump(self) -> None:
        self._pump_scheduled = False
        self.pump_events()

    def _handle_startup_action(self) -> None:
        requested = not self._startup_enabled
        result = self.coordinator.set_startup_enabled(requested)
        self.apply_startup_result(result)

    def apply_startup_result(self, result) -> None:
        if result.enabled is not None:
            self._startup_enabled = bool(result.enabled)
            try:
                self._post_startup_state(self._startup_enabled)
                self.settings_window.on_startup_state(self._startup_enabled)
            except (EngineUnhealthy, Exception) as error:
                self._handle_fatal(error)
                return
        if result.error is not None:
            self._report_error(result.error)

    def _post_startup_state(self, enabled: bool) -> None:
        post_state = getattr(self.tray, "post_startup_state", None)
        if callable(post_state):
            post_state(bool(enabled))
            return
        post_enabled = getattr(self.tray, "post_startup_enabled", None)
        if callable(post_enabled):
            post_enabled(bool(enabled))
            return
        from tray import TrayUpdate

        self.tray.post_update(TrayUpdate(startup_enabled=bool(enabled)))

    def _enter_fail_open(self) -> None:
        enter_fail_open = getattr(self.controller, "enter_fail_open", None)
        if callable(enter_fail_open):
            try:
                enter_fail_open()
            except BaseException as error:
                self._report_error(error)

    def _handle_fatal(self, error: BaseException) -> None:
        if self._fatal_handled:
            return
        self._fatal_handled = True
        self._enter_fail_open()
        self._report_error(error)
        self.shutdown()

    def _report_error(self, error: BaseException) -> None:
        try:
            self._show_error(error)
        except BaseException:
            pass

    def _destroy_root(self) -> None:
        if self._root_destroyed:
            return
        self._root_destroyed = True
        try:
            self.root.destroy()
        except BaseException:
            pass


def create_application(
    *,
    config_path: Path | str | None = None,
    executable: Path | str | None = None,
    resource_root: Path | str | None = None,
    factories: ApplicationFactories | object | None = None,
    command_timeout: float = DEFAULT_COMMAND_TIMEOUT,
    thread_timeout: float = DEFAULT_THREAD_TIMEOUT,
) -> AppLifecycle:
    """Compose the real application without starting any worker thread."""

    bundle = factories or ApplicationFactories()

    def default_root():
        import tkinter as tk

        return tk.Tk()

    root_factory = _factory(bundle, "root", default_root)
    root = root_factory()
    root.withdraw()

    try:
        from controller import CatModeController
        from hotkeys import parse_shortcut
        from keyboard_hook import KeyboardHook
        from settings import (
            StartupRegistry,
            WindowsRegistryAdapter,
            build_startup_command,
            load_settings,
            resolve_config_path,
            save_settings,
        )
        from settings_window import SettingsCoordinator, SettingsWindow
        from tray import NativeTray

        frozen = bool(getattr(sys, "frozen", False))
        executable_path = (
            Path(executable) if executable is not None else Path(sys.executable)
        )
        application_root = (
            executable_path.parent
            if frozen
            else Path(__file__).resolve().parent
        )
        if frozen:
            startup_executable = executable_path
            script_path = None
        else:
            sibling_pythonw = executable_path.parent / "pythonw.exe"
            startup_executable = (
                sibling_pythonw
                if sibling_pythonw.is_file()
                else executable_path.resolve()
            )
            script_path = Path(__file__).resolve()
        if config_path is None:
            local_appdata = Path(
                os.environ.get(
                    "LOCALAPPDATA",
                    Path.home() / "AppData" / "Local",
                )
            )
            current_config_path = resolve_config_path(
                application_root,
                local_appdata,
            )
        else:
            current_config_path = Path(config_path)
        current_settings = load_settings(current_config_path)
        shortcut = parse_shortcut(current_settings.toggle_hotkey)
        actions: queue.SimpleQueue = queue.SimpleQueue()

        def default_hook(value):
            return KeyboardHook(value)

        hook = _factory(bundle, "hook", default_hook)(shortcut)
        controller = _factory(
            bundle,
            "controller",
            lambda value: CatModeController(
                value,
                command_timeout=command_timeout,
            ),
        )(hook)
        startup_command = build_startup_command(
            startup_executable,
            script_path,
        )
        startup_registry = _factory(
            bundle,
            "startup_registry",
            lambda command: StartupRegistry(WindowsRegistryAdapter(), command),
        )(startup_command)
        startup_enabled = bool(startup_registry.is_enabled())

        icon_root = (
            Path(resource_root)
            if resource_root is not None
            else Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        )
        icon_path = icon_root / "assets" / "icon.ico"
        tray = _factory(
            bundle,
            "tray",
            lambda action_queue, enabled, notifications, icon: NativeTray(
                actions=action_queue,
                startup_enabled=enabled,
                notifications=notifications,
                icon_path=icon,
            ),
        )(actions, startup_enabled, current_settings.notifications, icon_path)

        persist = lambda settings: save_settings(current_config_path, settings)
        coordinator = _factory(
            bundle,
            "coordinator",
            SettingsCoordinator,
        )(
            current_settings,
            controller,
            persist,
            startup_registry,
            tray.post_notifications_enabled,
        )
        default_show_error = lambda error: _show_tk_error(root, error)
        show_error_factory = _factory(bundle, "show_error", None)
        if show_error_factory is None:
            show_error = default_show_error
        else:
            show_error = lambda error: show_error_factory(root, error)

        lifecycle_ref: dict[str, AppLifecycle] = {}

        def on_engine_unhealthy(error: EngineUnhealthy) -> None:
            lifecycle = lifecycle_ref.get("app")
            if lifecycle is None:
                show_error(error)
                return
            lifecycle._handle_fatal(error)

        def on_startup_result(result) -> None:
            lifecycle = lifecycle_ref.get("app")
            if lifecycle is None:
                return
            lifecycle.apply_startup_result(result)

        settings_window = _factory(
            bundle,
            "settings_window",
            SettingsWindow,
        )(
            root,
            coordinator,
            startup_enabled=startup_enabled,
            on_engine_unhealthy=on_engine_unhealthy,
            on_startup_result=on_startup_result,
        )

        lifecycle = AppLifecycle(
            root=root,
            hook=hook,
            controller=controller,
            tray=tray,
            coordinator=coordinator,
            settings_window=settings_window,
            actions=actions,
            show_error=show_error,
            command_timeout=command_timeout,
            thread_timeout=thread_timeout,
            startup_registry=startup_registry,
        )
        lifecycle_ref["app"] = lifecycle
        return lifecycle
    except BaseException:
        try:
            root.destroy()
        except BaseException:
            pass
        raise


def _show_tk_error(root: object, error: BaseException) -> None:
    from tkinter import messagebox

    messagebox.showerror("CatLocker", str(error), parent=root)


if __name__ == "__main__":
    create_application().run()
