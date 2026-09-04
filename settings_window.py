from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from controller import EngineUnhealthy
from hotkeys import (
    SUPPORTED_MODIFIER_VKS,
    Shortcut,
    ShortcutError,
    parse_shortcut,
    shortcut_from_pressed_vks,
    validate_shortcut,
)
from settings import AppSettings


class SettingsLocked(RuntimeError):
    """Raised when settings are changed while Cat Mode is locked."""


class WarningDeclined(RuntimeError):
    """Raised when the user declines a shortcut warning."""


@dataclass(frozen=True, slots=True)
class StartupUpdateResult:
    enabled: bool | None
    error: OSError | None = None


class SettingsCoordinator:
    def __init__(
        self,
        current: AppSettings,
        controller: object,
        persist: Callable[[AppSettings], None],
        startup_registry: object,
        apply_notifications: Callable[[bool], None],
    ) -> None:
        self._current = current
        self.controller = controller
        self._persist = persist
        self._startup_registry = startup_registry
        self._apply_notifications = apply_notifications
        self._recording = False
        self._pressed_vks: set[int] = set()

    @property
    def current(self) -> AppSettings:
        return self._current

    @property
    def recording(self) -> bool:
        return self._recording

    def save(
        self,
        toggle_hotkey: str,
        notifications: bool,
        *,
        confirm_warning: Callable[[tuple[str, ...]], bool] | None = None,
    ) -> AppSettings:
        if self.controller.locked:
            raise SettingsLocked("Settings are unavailable while Cat Mode is locked.")

        if not isinstance(toggle_hotkey, str):
            raise ShortcutError("A shortcut must be text.")
        validation = validate_shortcut(parse_shortcut(toggle_hotkey))
        if validation.warnings:
            if confirm_warning is None or not confirm_warning(validation.warnings):
                raise WarningDeclined("Shortcut warning was not confirmed.")

        if self._recording:
            self.end_recording()

        candidate = AppSettings(
            toggle_hotkey=validation.shortcut.canonical,
            notifications=bool(notifications),
        )
        previous = self._current
        try:
            accepted = self.controller.replace_shortcut(validation.shortcut)
        except EngineUnhealthy:
            self._clear_recording()
            raise
        if accepted is None:
            raise SettingsLocked("The keyboard engine rejected the shortcut.")

        try:
            self._persist(candidate)
        except Exception:
            try:
                rollback = self.controller.replace_shortcut(
                    parse_shortcut(previous.toggle_hotkey)
                )
            except EngineUnhealthy:
                self._clear_recording()
                raise
            if rollback is None:
                self._clear_recording()
                self.controller.enter_fail_open()
                raise EngineUnhealthy("shortcut rollback rejected")
            raise

        self._current = candidate
        self._apply_notifications(candidate.notifications)
        return candidate

    def set_startup_enabled(self, enabled: bool) -> StartupUpdateResult:
        write_error: OSError | None = None
        try:
            self._startup_registry.set_enabled(bool(enabled))
        except OSError as exc:
            write_error = exc

        try:
            actual = bool(self._startup_registry.is_enabled())
        except OSError as refresh_error:
            if write_error is not None:
                return StartupUpdateResult(None, write_error)
            return StartupUpdateResult(None, refresh_error)
        return StartupUpdateResult(actual, write_error)

    def begin_recording(self) -> bool:
        if self._recording:
            return True
        try:
            accepted = bool(self.controller.enter_recording())
        except EngineUnhealthy:
            self._clear_recording()
            raise
        if not accepted:
            self._clear_recording()
            return False
        self._pressed_vks.clear()
        self._recording = True
        return True

    def record_keydown(self, vk: int) -> Shortcut | None:
        if not self._recording:
            return None
        vk = int(vk)
        if vk in self._pressed_vks:
            return None
        self._pressed_vks.add(vk)
        if vk in SUPPORTED_MODIFIER_VKS:
            return None
        try:
            shortcut = shortcut_from_pressed_vks(
                self._pressed_vks,
                trigger_vk=vk,
            )
        except ShortcutError:
            return None
        self.end_recording()
        return shortcut

    def record_keyup(self, vk: int) -> None:
        if self._recording:
            self._pressed_vks.discard(int(vk))

    def end_recording(self) -> bool:
        try:
            return bool(self.controller.exit_recording())
        except EngineUnhealthy:
            self._clear_recording()
            raise
        finally:
            self._clear_recording()

    def _clear_recording(self) -> None:
        self._recording = False
        self._pressed_vks.clear()


class SettingsViewModel:
    def __init__(
        self,
        coordinator: SettingsCoordinator,
        *,
        startup_enabled: bool = False,
    ) -> None:
        self.coordinator = coordinator
        self.hotkey_text = coordinator.current.toggle_hotkey
        self.notifications = coordinator.current.notifications
        self.startup_enabled = bool(startup_enabled)
        self._accepted_hotkey = self.hotkey_text
        self._recording = False
        self._locked = bool(coordinator.controller.locked)

    @property
    def locked(self) -> bool:
        return self._locked or bool(self.coordinator.controller.locked)

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def record_label(self) -> str:
        return "Press a shortcut..." if self.recording else "Record shortcut"

    @property
    def hotkey_enabled(self) -> bool:
        return not self.locked

    @property
    def record_enabled(self) -> bool:
        return not self.locked and not self.recording

    @property
    def save_enabled(self) -> bool:
        return not self.locked

    def begin_recording(self) -> bool:
        if self.locked:
            return False
        try:
            accepted = self.coordinator.begin_recording()
        except EngineUnhealthy:
            self._recording = False
            raise
        if not accepted:
            return False
        self._recording = True
        return True

    def on_key_press(self, event) -> Shortcut | None:
        if not self._recording:
            return None
        try:
            shortcut = self.coordinator.record_keydown(int(event.keycode))
        except EngineUnhealthy:
            self._recording = False
            raise
        if shortcut is None:
            return None
        self.hotkey_text = shortcut.canonical
        self._recording = False
        return shortcut

    def on_key_release(self, event) -> None:
        if self._recording:
            self.coordinator.record_keyup(int(event.keycode))

    def cancel_recording(self) -> None:
        if not self._recording and not self.coordinator.recording:
            return
        try:
            self.coordinator.end_recording()
        finally:
            self._recording = False

    def on_focus_out(self, _event=None) -> None:
        self.cancel_recording()

    def on_close(self) -> None:
        self.cancel_recording()
        self.hotkey_text = self._accepted_hotkey
        self.notifications = self.coordinator.current.notifications

    def save(
        self,
        *,
        confirm_warning: Callable[[tuple[str, ...]], bool] | None = None,
    ) -> AppSettings:
        previous_display = self._accepted_hotkey
        if self._recording:
            self.cancel_recording()
        try:
            saved = self.coordinator.save(
                self.hotkey_text,
                self.notifications,
                confirm_warning=confirm_warning,
            )
        except Exception:
            self.hotkey_text = previous_display
            raise
        self.hotkey_text = saved.toggle_hotkey
        self.notifications = saved.notifications
        self._accepted_hotkey = saved.toggle_hotkey
        return saved

    def set_startup_enabled(self, enabled: bool) -> StartupUpdateResult:
        result = self.coordinator.set_startup_enabled(enabled)
        if result.enabled is not None:
            self.startup_enabled = bool(result.enabled)
        return result

    def on_lock_state(self, locked: bool) -> None:
        self._locked = bool(locked)
        if self._locked:
            self.cancel_recording()


class SettingsWindow:
    """A small Tk view attached to an existing main-thread Tk root."""

    def __init__(
        self,
        root,
        coordinator: SettingsCoordinator,
        *,
        startup_enabled: bool = False,
        view_model: SettingsViewModel | None = None,
        on_engine_unhealthy: Callable[[EngineUnhealthy], object] | None = None,
        on_startup_result: Callable[[StartupUpdateResult], object] | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import messagebox

        self.root = root
        self._tk = tk
        self._messagebox = messagebox
        self._on_engine_unhealthy = on_engine_unhealthy
        self._on_startup_result = on_startup_result
        self.view = view_model or SettingsViewModel(
            coordinator,
            startup_enabled=startup_enabled,
        )
        self.window = tk.Toplevel(root)
        self.window.title("CatLocker Settings")
        self.window.withdraw()
        self.window.protocol("WM_DELETE_WINDOW", self._close)

        self.hotkey_var = tk.StringVar(self.window, value=self.view.hotkey_text)
        self.notifications_var = tk.BooleanVar(
            self.window,
            value=self.view.notifications,
        )
        self.startup_var = tk.BooleanVar(
            self.window,
            value=self.view.startup_enabled,
        )
        self.status_var = tk.StringVar(self.window, value="")

        frame = tk.Frame(self.window, padx=12, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Toggle shortcut:").pack(anchor="w")
        self.hotkey_entry = tk.Entry(frame, textvariable=self.hotkey_var)
        self.hotkey_entry.pack(fill="x", pady=(0, 8))
        buttons = tk.Frame(frame)
        buttons.pack(fill="x")
        self.record_button = tk.Button(
            buttons,
            text=self.view.record_label,
            command=self._record,
        )
        self.record_button.pack(side="left")
        self.save_button = tk.Button(buttons, text="Save", command=self._save)
        self.save_button.pack(side="left", padx=(8, 0))
        self.cancel_button = tk.Button(
            buttons,
            text="Cancel",
            command=self._close,
        )
        self.cancel_button.pack(side="right")
        tk.Checkbutton(
            frame,
            text="Start with Windows",
            variable=self.startup_var,
            command=self._startup_changed,
        ).pack(anchor="w", pady=(12, 0))
        tk.Checkbutton(
            frame,
            text="Show notifications",
            variable=self.notifications_var,
        ).pack(anchor="w")
        tk.Label(frame, textvariable=self.status_var).pack(anchor="w", pady=(8, 0))

        self._recording_bindings: list[tuple[object, str, str]] = []
        self._sync_controls()

    def show(self) -> None:
        if self.view.locked:
            return
        self.hotkey_var.set(self.view.hotkey_text)
        self.notifications_var.set(self.view.notifications)
        self.startup_var.set(self.view.startup_enabled)
        self.window.deiconify()
        self.window.lift()
        self.hotkey_entry.focus_set()

    def on_lock_state(self, locked: bool) -> None:
        try:
            try:
                self.view.on_lock_state(locked)
            except EngineUnhealthy as exc:
                self._handle_engine_unhealthy(exc)
        finally:
            self._unbind_recording_events()
            self._sync_controls()
            if locked:
                self.window.withdraw()

    def on_startup_state(self, enabled: bool) -> None:
        self.view.startup_enabled = bool(enabled)
        self.startup_var.set(self.view.startup_enabled)

    def _record(self) -> None:
        try:
            accepted = self.view.begin_recording()
        except EngineUnhealthy as exc:
            self._handle_engine_unhealthy(exc)
            return
        if accepted:
            self._bind_recording_events()
            self.status_var.set("Press one shortcut combination.")
        self._sync_controls()

    def _save(self) -> None:
        self.view.hotkey_text = self.hotkey_var.get()
        self.view.notifications = bool(self.notifications_var.get())

        def confirm_warning(messages: tuple[str, ...]) -> bool:
            return self._messagebox.askyesno(
                "Confirm shortcut",
                "\n".join(messages),
                parent=self.window,
            )

        try:
            self.view.save(confirm_warning=confirm_warning)
        except WarningDeclined:
            self.hotkey_var.set(self.view.hotkey_text)
            return
        except EngineUnhealthy as exc:
            self.hotkey_var.set(self.view.hotkey_text)
            self._handle_engine_unhealthy(exc)
            return
        except Exception as exc:
            self.hotkey_var.set(self.view.hotkey_text)
            from tray import TrayStopped

            if isinstance(exc, TrayStopped):
                self._handle_engine_unhealthy(exc)
            else:
                self._show_error(exc)
            return
        self.hotkey_var.set(self.view.hotkey_text)
        self.status_var.set("Settings saved.")

    def _startup_changed(self) -> None:
        result = self.view.set_startup_enabled(bool(self.startup_var.get()))
        self.startup_var.set(self.view.startup_enabled)
        if self._on_startup_result is not None:
            self._on_startup_result(result)
        elif result.error is not None:
            self._show_error(result.error)

    def _close(self) -> None:
        try:
            self.view.on_close()
        except EngineUnhealthy as exc:
            self._handle_engine_unhealthy(exc)
            return
        self._unbind_recording_events()
        self.window.withdraw()

    def _on_key_press(self, event) -> str:
        try:
            shortcut = self.view.on_key_press(event)
        except EngineUnhealthy as exc:
            self._handle_engine_unhealthy(exc)
            self._unbind_recording_events()
            self._sync_controls()
            return
        if shortcut is not None:
            self.hotkey_var.set(self.view.hotkey_text)
            self.status_var.set("Shortcut captured. Save to apply it.")
            self._unbind_recording_events()
            self._sync_controls()
        return "break"

    def _on_key_release(self, event) -> None:
        self.view.on_key_release(event)

    def _on_focus_out(self, _event=None) -> None:
        try:
            self.view.on_focus_out()
        except EngineUnhealthy as exc:
            self._handle_engine_unhealthy(exc)
        finally:
            self._unbind_recording_events()
            self._sync_controls()

    def _bind_recording_events(self) -> None:
        self._recording_bindings = [
            (
                self.hotkey_entry,
                "<KeyPress>",
                self.hotkey_entry.bind("<KeyPress>", self._on_key_press),
            ),
            (
                self.hotkey_entry,
                "<KeyRelease>",
                self.hotkey_entry.bind("<KeyRelease>", self._on_key_release),
            ),
            (
                self.hotkey_entry,
                "<FocusOut>",
                self.hotkey_entry.bind("<FocusOut>", self._on_focus_out),
            ),
        ]
        self.window.protocol("WM_DELETE_WINDOW", self._close)

    def _unbind_recording_events(self) -> None:
        for widget, sequence, binding_id in self._recording_bindings:
            widget.unbind(sequence, binding_id)
        self._recording_bindings.clear()

    def _handle_engine_unhealthy(self, error: EngineUnhealthy) -> None:
        if self._on_engine_unhealthy is not None:
            self._on_engine_unhealthy(error)
            return
        self._show_error(error)

    def _sync_controls(self) -> None:
        self.record_button.configure(
            text=self.view.record_label,
            state="normal" if self.view.record_enabled else "disabled",
        )
        self.save_button.configure(
            state="normal" if self.view.save_enabled else "disabled",
        )
        self.hotkey_entry.configure(
            state="normal" if self.view.hotkey_enabled else "disabled",
        )

    def _show_error(self, error: BaseException) -> None:
        self._messagebox.showerror("CatLocker Settings", str(error), parent=self.window)
