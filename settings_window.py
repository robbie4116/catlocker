from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from controller import EngineUnhealthy
from hotkeys import (
    Shortcut,
    ShortcutError,
    VK_LCONTROL,
    VK_LMENU,
    VK_LSHIFT,
    VK_LWIN,
    VK_RCONTROL,
    VK_RMENU,
    VK_RSHIFT,
    VK_RWIN,
    format_pressed_vks,
    format_shortcut,
    parse_shortcut,
    shortcut_from_pressed_vks,
    validate_shortcut,
    VK_NAMES,
)
from recording_channel import RecordingChannel
from shortcut_recorder import ShortcutRecorder
from settings import AppSettings


class SettingsLocked(RuntimeError):
    """Raised when settings are changed while Cat Mode is locked."""


class MalformedTkEvent(ValueError):
    """Raised by the legacy test adapter when a Tk event has no keycode."""


class WarningDeclined(RuntimeError):
    """Raised when the user declines a shortcut warning."""


# Kept as a compatibility helper for callers that used the old view-model API.
# SettingsWindow never uses it: native hook events are authoritative there.
_TK_KEYSYM_TO_VK = {
    "shift_l": VK_LSHIFT,
    "shift_r": VK_RSHIFT,
    "control_l": VK_LCONTROL,
    "control_r": VK_RCONTROL,
    "alt_l": VK_LMENU,
    "alt_r": VK_RMENU,
    "win_l": VK_LWIN,
    "win_r": VK_RWIN,
    "super_l": VK_LWIN,
    "super_r": VK_RWIN,
    "meta_l": VK_LWIN,
    "meta_r": VK_RWIN,
    "shift": VK_LSHIFT,
    "control": VK_LCONTROL,
    "ctrl": VK_LCONTROL,
    "alt": VK_LMENU,
    "win": VK_LWIN,
    "super": VK_LWIN,
    "meta": VK_LWIN,
}
_GENERIC_TK_KEYCODES_TO_VK = {0x10: VK_LSHIFT, 0x11: VK_LCONTROL, 0x12: VK_LMENU}


@lru_cache(maxsize=1)
def _windows_user32():
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetKeyboardLayout.argtypes = [ctypes.c_ulong]
    user32.GetKeyboardLayout.restype = ctypes.c_void_p
    user32.MapVirtualKeyExW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    user32.MapVirtualKeyExW.restype = ctypes.c_uint
    user32.GetKeyNameTextW.argtypes = [
        ctypes.c_long,
        ctypes.POINTER(ctypes.c_wchar),
        ctypes.c_int,
    ]
    user32.GetKeyNameTextW.restype = ctypes.c_int
    return user32


def windows_key_label_resolver(token: str) -> str | None:
    """Resolve a display label on the UI thread without changing the saved token."""

    user32 = _windows_user32()
    vk = VK_NAMES.get(token)
    if user32 is None or vk is None:
        return None
    scan_code = int(user32.MapVirtualKeyExW(vk, 4, user32.GetKeyboardLayout(0)))
    if scan_code == 0:
        return None
    lparam = (scan_code & 0xFF) << 16
    if scan_code & 0xFF00:
        lparam |= 1 << 24
    buffer = ctypes.create_unicode_buffer(64)
    length = int(user32.GetKeyNameTextW(lparam, buffer, len(buffer)))
    if length <= 0:
        return None
    label = buffer.value.strip()
    if label.casefold() in {
        token.casefold(),
        token.replace("_", " ").casefold(),
    }:
        return None
    if token == "OEM_3" and label in {"`", "~", "Backquote", "Backtick"}:
        return "Backtick"
    return label or None


def normalize_tk_event(event) -> int:
    keysym = str(getattr(event, "keysym", "") or "").strip().casefold()
    if keysym in _TK_KEYSYM_TO_VK:
        return _TK_KEYSYM_TO_VK[keysym]
    try:
        keycode = int(getattr(event, "keycode"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise MalformedTkEvent("Tk event must have a numeric keycode.") from exc
    return _GENERIC_TK_KEYCODES_TO_VK.get(keycode, keycode)


@dataclass(frozen=True, slots=True)
class StartupUpdateResult:
    enabled: bool | None
    error: OSError | None = None


@dataclass(frozen=True, slots=True)
class RecordingUpdate:
    preview: str
    candidate: Shortcut | None
    explanation: str | None
    recording: bool


# Sentinel distinguishing "caller did not pass toggle_hotkey at all" from an explicit
# ``None``, mirroring the ``_UNSET`` convention in settings.py. save()'s own default
# (``None``) already carries meaning for lock/unlock-style fields, so toggle_hotkey needs
# a value that no real caller could ever legitimately pass.
_UNSET = object()


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
        self._session = None
        self._channel: RecordingChannel | None = None
        self._recorder: ShortcutRecorder | None = None
        self._candidate: Shortcut | None = None
        self._explanation: str | None = None

    @property
    def current(self) -> AppSettings:
        return self._current

    @property
    def recording(self) -> bool:
        return self._recording

    @property
    def native_recording(self) -> bool:
        return callable(getattr(self.controller, "begin_recording", None))

    @property
    def recording_session_id(self):
        return None if self._session is None else self._session.session_id

    @property
    def recording_text(self) -> str:
        if not self._recording or self._recorder is None:
            return ""
        return self._recorder.held_preview

    @property
    def recording_keys(self) -> frozenset[int]:
        if not self._recording or self._recorder is None:
            return frozenset()
        return self._recorder.held_keys

    @property
    def recording_explanation(self) -> str | None:
        return self._explanation if self._recorder is None else self._recorder.explanation

    @property
    def pending_shortcut(self) -> Shortcut | None:
        return self._candidate

    def save(
        self,
        lock_hotkey: str,
        notifications: bool,
        *,
        confirm_warning: Callable[[tuple[str, ...]], bool] | None = None,
        unlock_hotkey: str | None = None,
        separate_shortcuts: bool | None = None,
        toggle_hotkey: str | None = _UNSET,
    ) -> AppSettings:
        if self.controller.locked:
            raise SettingsLocked("Settings are unavailable while Cat Mode is locked.")

        if toggle_hotkey is _UNSET:
            # No fresh toggle draft was supplied. An explicit mode call (separate_shortcuts
            # given) is the modern API and simply left the toggle field untouched, so the
            # previously stored toggle binding must survive this save unchanged. A fully
            # legacy call (mode also omitted) keeps delegating to AppSettings' own
            # pair-only inference below, exactly as it did before this sentinel existed.
            toggle_hotkey = self._current.toggle_hotkey if separate_shortcuts is not None else None

        if self._recording:
            self.end_recording()
        shortcut = self._candidate if unlock_hotkey is None else None
        if shortcut is None:
            if not isinstance(lock_hotkey, str):
                raise ShortcutError("A shortcut must be text.")
            shortcut = validate_shortcut(parse_shortcut(lock_hotkey)).shortcut
        validation = validate_shortcut(shortcut)
        unlock_validation = validate_shortcut(parse_shortcut(unlock_hotkey)) if unlock_hotkey is not None else validation
        # The toggle binding is validated too even when it is not the active pair right
        # now: Saving validates all stored bindings, not just the ones currently in use.
        toggle_validation = validate_shortcut(parse_shortcut(toggle_hotkey)) if toggle_hotkey is not None else None

        warning_groups = [validation.warnings, unlock_validation.warnings]
        if toggle_validation is not None:
            warning_groups.append(toggle_validation.warnings)
        warnings = tuple(dict.fromkeys(warning for group in warning_groups for warning in group))
        if warnings:
            if confirm_warning is None or not confirm_warning(warnings):
                raise WarningDeclined("Shortcut warning was not confirmed.")

        candidate = AppSettings(
            toggle_hotkey=(toggle_validation.shortcut.canonical if toggle_validation is not None else None),
            notifications=bool(notifications),
            lock_hotkey=validation.shortcut.canonical,
            unlock_hotkey=unlock_validation.shortcut.canonical,
            separate_shortcuts=separate_shortcuts,
        )
        previous = self._current
        try:
            accepted = self.controller.replace_shortcut(candidate.active_shortcuts())
        except EngineUnhealthy:
            self._clear_candidate()
            raise
        if accepted is None:
            raise SettingsLocked("The keyboard engine rejected the shortcut.")

        try:
            self._persist(candidate)
        except Exception:
            try:
                rollback = self.controller.replace_shortcut(previous.active_shortcuts())
            except EngineUnhealthy:
                self._clear_candidate()
                raise
            if rollback is None:
                self._clear_candidate()
                self.controller.enter_fail_open()
                raise EngineUnhealthy("shortcut rollback rejected")
            self._clear_candidate()
            raise

        self._current = candidate
        self._clear_candidate()
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
            begin = getattr(self.controller, "begin_recording", None)
            if callable(begin):
                session = begin()
                if session is None:
                    self._clear_recording()
                    return False
                self._session = session
                self._channel = session.channel
                held_keys = session.held_keys
            else:
                if not bool(self.controller.enter_recording()):
                    self._clear_recording()
                    return False
                held_keys = frozenset()
                self._channel = RecordingChannel("legacy-settings")
        except EngineUnhealthy:
            self._clear_recording()
            raise
        self._recorder = ShortcutRecorder(
            self.recording_session_id or "legacy-settings",
            held_keys=held_keys,
        )
        self._candidate = None
        self._explanation = None
        self._recording = True
        return True

    def poll_recording(self, *, limit: int = 64) -> RecordingUpdate:
        if not self._recording or self._recorder is None:
            return RecordingUpdate("", self._candidate, self._explanation, False)
        if self._channel is None:
            return self._legacy_update()
        status = self._channel.status
        if not status.healthy:
            explanation = (
                "Recording overflowed. Try recording again."
                if status.overflowed
                else "Recording was cancelled. Try again."
            )
            self._cancel_native_recording()
            self._explanation = explanation
            return RecordingUpdate("", None, explanation, False)

        for envelope in self._channel.drain(limit=limit):
            if envelope.session_id != self.recording_session_id:
                continue
            self._recorder.consume(envelope.event)
        if self._recorder.candidate is not None:
            status = self._channel.status
            if not status.healthy:
                return self._reject_unhealthy_recording(status)
            try:
                accepted = bool(self.controller.finish_recording(self.recording_session_id))
            except EngineUnhealthy:
                self._clear_recording()
                raise
            if not accepted:
                self._clear_recording()
                self._explanation = "Recording could not be completed. Try again."
                return RecordingUpdate("", None, self._explanation, False)
            self._candidate = self._recorder.candidate
            preview = self._recorder.held_preview
            self._session = None
            self._channel = None
            self._recording = False
            return RecordingUpdate(preview, self._candidate, None, False)
        return RecordingUpdate(
            self.recording_text,
            None,
            self._recorder.explanation,
            True,
        )

    def _legacy_update(self) -> RecordingUpdate:
        return RecordingUpdate(
            self.recording_text,
            self._recorder.candidate if self._recorder else None,
            self.recording_explanation,
            self._recording,
        )

    def _reject_unhealthy_recording(self, status) -> RecordingUpdate:
        explanation = (
            "Recording overflowed. Try recording again."
            if status.overflowed
            else "Recording was cancelled. Try again."
        )
        self._cancel_native_recording()
        self._explanation = explanation
        return RecordingUpdate("", None, explanation, False)

    def record_keydown(self, vk: int) -> Shortcut | None:
        """Compatibility path used only by legacy non-native test doubles."""
        if not self._recording or self._recorder is None:
            return None
        from hotkeys import KeyEvent

        self._recorder.consume(KeyEvent(int(vk), True))
        return self._complete_legacy_candidate()

    def record_keyup(self, vk: int) -> Shortcut | None:
        if not self._recording or self._recorder is None:
            return None
        from hotkeys import KeyEvent

        self._recorder.consume(KeyEvent(int(vk), False))
        return self._complete_legacy_candidate()

    def _complete_legacy_candidate(self) -> Shortcut | None:
        if self._recorder is None or self._recorder.candidate is None:
            return None
        try:
            accepted = bool(self.controller.exit_recording())
        except EngineUnhealthy:
            self._clear_recording()
            raise
        candidate = self._recorder.candidate
        self._recording = False
        self._channel = None
        self._session = None
        if accepted:
            self._candidate = candidate
            return candidate
        return candidate

    def end_recording(self) -> bool:
        if not self._recording:
            return True
        try:
            if self._session is not None:
                result = bool(self.controller.cancel_recording(self._session.session_id))
            else:
                result = bool(self.controller.exit_recording())
        except EngineUnhealthy:
            self._clear_recording()
            raise
        finally:
            self._clear_recording()
        return result

    def discard_candidate(self) -> None:
        self._candidate = None

    def _cancel_native_recording(self) -> None:
        session = self._session
        if session is not None:
            try:
                self.controller.cancel_recording(session.session_id)
            except EngineUnhealthy:
                self._clear_recording()
                raise
        self._clear_recording()

    def _clear_candidate(self) -> None:
        self._candidate = None

    def _clear_recording(self) -> None:
        self._recording = False
        self._session = None
        self._channel = None
        self._recorder = None


class SettingsViewModel:
    def __init__(
        self,
        coordinator: SettingsCoordinator,
        *,
        startup_enabled: bool = False,
        key_label_resolver: Callable[[str], str | None] | None = None,
    ) -> None:
        self.coordinator = coordinator
        self._key_label_resolver = key_label_resolver
        self.action = "lock"
        self._drafts = {"lock": coordinator.current.lock_hotkey, "unlock": coordinator.current.unlock_hotkey}
        self._accepted_canonical = coordinator.current.lock_hotkey
        self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))
        self.notifications = coordinator.current.notifications
        self.startup_enabled = bool(startup_enabled)
        self._recording = False
        self._locked = bool(coordinator.controller.locked)

    def _capture_draft(self):
        candidate = self.coordinator.pending_shortcut
        if candidate is not None:
            self._drafts[self.action] = candidate.canonical
            self.coordinator.discard_candidate()

    def select_action(self, action):
        if action not in ("lock", "unlock"):
            raise ValueError(action)
        self.cancel_recording()
        self._capture_draft()
        self.action = action
        self._accepted_canonical = self._drafts[action]
        self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))

    def _display_shortcut(self, shortcut: Shortcut) -> str:
        return format_shortcut(
            shortcut,
            key_label_resolver=self._key_label_resolver,
        )

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
        return not self.locked and not self.recording

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
        self.hotkey_text = ""
        return True

    def refresh_recording(self) -> RecordingUpdate:
        if not self._recording:
            return RecordingUpdate("", self.coordinator.pending_shortcut, None, False)
        update = self.coordinator.poll_recording()
        if update.candidate is not None:
            self.hotkey_text = self._display_shortcut(update.candidate)
            self._recording = False
        elif update.recording:
            preview = format_pressed_vks(
                self.coordinator.recording_keys,
                preserve_modifier_sides=True,
                key_label_resolver=self._key_label_resolver,
            )
            self.hotkey_text = preview.replace("+", " + ")
        else:
            self._recording = False
            self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))
        return update

    def on_key_press(self, event) -> Shortcut | None:
        if not self._recording or self.coordinator.native_recording:
            return None
        vk = normalize_tk_event(event)
        try:
            shortcut = self.coordinator.record_keydown(vk)
        except Exception:
            self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))
            self._recording = False
            raise
        if shortcut is not None:
            self.hotkey_text = self._display_shortcut(shortcut)
            self._recording = False
        else:
            self.hotkey_text = self.coordinator.recording_text.replace("+", " + ")
        return shortcut

    def on_key_release(self, event) -> None:
        if self._recording and not self.coordinator.native_recording:
            vk = normalize_tk_event(event)
            try:
                shortcut = self.coordinator.record_keyup(vk)
            except Exception:
                self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))
                self._recording = False
                raise
            if shortcut is not None:
                self.hotkey_text = self._display_shortcut(shortcut)
                self._recording = False
            else:
                self.hotkey_text = self.coordinator.recording_text.replace("+", " + ")

    def cancel_recording(self) -> None:
        if not self._recording and not self.coordinator.recording:
            return
        try:
            self.coordinator.end_recording()
        finally:
            self._recording = False
            self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))

    def on_focus_out(self, _event=None) -> None:
        self.cancel_recording()

    def on_close(self) -> None:
        try:
            self.cancel_recording()
            self.coordinator.discard_candidate()
        finally:
            self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))
            self.notifications = self.coordinator.current.notifications
            self._drafts = {"lock": self.coordinator.current.lock_hotkey, "unlock": self.coordinator.current.unlock_hotkey}
            self._accepted_canonical = self._drafts[self.action]
            self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))

    def save(
        self,
        *,
        confirm_warning: Callable[[tuple[str, ...]], bool] | None = None,
    ) -> AppSettings:
        if self._recording:
            self.cancel_recording()
        self._capture_draft()
        try:
            saved = self.coordinator.save(
                self._drafts["lock"],
                self.notifications,
                confirm_warning=confirm_warning,
                unlock_hotkey=self._drafts["unlock"],
            )
        except Exception:
            self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))
            raise
        self._drafts = {"lock": saved.lock_hotkey, "unlock": saved.unlock_hotkey}
        self._accepted_canonical = self._drafts[self.action]
        self.hotkey_text = self._display_shortcut(parse_shortcut(self._accepted_canonical))
        self.notifications = saved.notifications
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
            self.coordinator.discard_candidate()


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
        key_label_resolver: Callable[[str], str | None] | None = None,
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
            key_label_resolver=(
                windows_key_label_resolver
                if key_label_resolver is None
                else key_label_resolver
            ),
        )
        self._native_recording = self.view.coordinator.native_recording
        self.window = tk.Toplevel(root)
        self.window.title("CatLocker Settings")
        self.window.withdraw()
        self.window.protocol("WM_DELETE_WINDOW", self._close)

        self.hotkey_var = tk.StringVar(self.window, value=self.view.hotkey_text)
        self.notifications_var = tk.BooleanVar(self.window, value=self.view.notifications)
        self.startup_var = tk.BooleanVar(self.window, value=self.view.startup_enabled)
        self.status_var = tk.StringVar(self.window, value="")

        frame = tk.Frame(self.window, padx=12, pady=12)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="Lock keyboard:").pack(anchor="w")
        self.hotkey_entry = tk.Entry(frame, textvariable=self.hotkey_var)
        self.hotkey_entry.pack(fill="x", pady=(0, 8))
        self.record_button = tk.Button(frame, text="Record lock shortcut", command=lambda: self._record_action("lock"))
        self.record_button.pack(anchor="w")
        tk.Label(frame, text="Unlock keyboard:").pack(anchor="w", pady=(12, 0))
        self.unlock_var = tk.StringVar(self.window, value=self.view._display_shortcut(parse_shortcut(coordinator.current.unlock_hotkey)))
        self.unlock_entry = tk.Entry(frame, textvariable=self.unlock_var)
        self.unlock_entry.pack(fill="x", pady=(0, 8))
        self.unlock_button = tk.Button(frame, text="Record unlock shortcut", command=lambda: self._record_action("unlock"))
        self.unlock_button.pack(anchor="w")
        tk.Label(frame, text="Use the same shortcut for both to toggle.").pack(anchor="w", pady=(8, 0))
        self._shortcut_rows = {
            "lock": (self.hotkey_var, self.hotkey_entry, self.record_button),
            "unlock": (self.unlock_var, self.unlock_entry, self.unlock_button),
        }

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
        buttons = tk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        self.save_button = tk.Button(buttons, text="Save", command=self._save)
        self.save_button.pack(side="left")
        self.cancel_button = tk.Button(buttons, text="Cancel", command=self._close)
        self.cancel_button.pack(side="right")


        self._recording_bindings: list[tuple[object, str, str]] = []
        self._recording_poll_id = None
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
        error: BaseException | None = None
        try:
            self.view.on_lock_state(locked)
        except EngineUnhealthy as exc:
            error = exc
        except Exception as exc:
            error = exc
        finally:
            self._unbind_recording_events()
            self._cancel_recording_poll()
            self.hotkey_var.set(self.view.hotkey_text)
            self._sync_controls()
            if locked:
                self.window.withdraw()
        if isinstance(error, EngineUnhealthy):
            self._handle_engine_unhealthy(error)
        elif error is not None:
            self._show_error(error)

    def on_startup_state(self, enabled: bool) -> None:
        self.view.startup_enabled = bool(enabled)
        self.startup_var.set(self.view.startup_enabled)

    def _record_action(self, action):
        self._cleanup_recording_state()
        self.view.select_action(action)
        self.hotkey_var, self.hotkey_entry, self.record_button = self._shortcut_rows[action]
        self.hotkey_var.set(self.view.hotkey_text)
        self._record()

    def _record(self) -> None:
        try:
            accepted = self.view.begin_recording()
        except EngineUnhealthy as exc:
            self._sync_controls()
            self._handle_engine_unhealthy(exc)
            return
        except Exception as exc:
            self._sync_controls()
            self._show_error(exc)
            return
        if accepted:
            try:
                self._bind_recording_events()
            except Exception as exc:
                self._cleanup_recording_state()
                self._show_error(exc)
                return
            self.hotkey_entry.focus_set()
            self.hotkey_var.set(self.view.hotkey_text)
            self.status_var.set(
                "Press one shortcut combination. Some Fn combinations may not be detected. Try another key if nothing appears."
            )
            self._schedule_recording_poll()
        self._sync_controls()

    def _poll_recording(self, session_id) -> None:
        self._recording_poll_id = None
        if not self.view.recording or session_id != self.view.coordinator.recording_session_id:
            return
        try:
            update = self.view.refresh_recording()
        except EngineUnhealthy as exc:
            self._cleanup_recording_state()
            self._handle_engine_unhealthy(exc)
            return
        except Exception as exc:
            self._cleanup_recording_state()
            self._show_error(exc)
            return
        self.hotkey_var.set(self.view.hotkey_text)
        if update.explanation:
            self.status_var.set(update.explanation)
        elif update.candidate is not None:
            self.status_var.set("Shortcut captured. Save to apply it.")
            self._unbind_recording_events()
        elif update.recording:
            self.status_var.set("Press one shortcut combination.")
        if self.view.recording:
            self._schedule_recording_poll()
        else:
            self._unbind_recording_events()
        self._sync_controls()

    def _schedule_recording_poll(self) -> None:
        after = getattr(self.root, "after", None)
        if after is None or not self.view.recording or self._recording_poll_id is not None:
            return
        session_id = self.view.coordinator.recording_session_id
        self._recording_poll_id = after(
            10,
            lambda sid=session_id: self._poll_recording(sid),
        )

    def _cancel_recording_poll(self) -> None:
        callback_id = self._recording_poll_id
        self._recording_poll_id = None
        after_cancel = getattr(self.root, "after_cancel", None)
        if callback_id is not None and after_cancel is not None:
            after_cancel(callback_id)

    def _save(self) -> None:
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
        error: BaseException | None = None
        try:
            self.view.on_close()
        except EngineUnhealthy as exc:
            error = exc
        except Exception as exc:
            error = exc
        finally:
            self._unbind_recording_events()
            self._cancel_recording_poll()
            self.hotkey_var.set(self.view.hotkey_text)
            self._sync_controls()
            self.window.withdraw()
        if isinstance(error, EngineUnhealthy):
            self._handle_engine_unhealthy(error)
        elif error is not None:
            self._show_error(error)

    def _on_key_press(self, event) -> str:
        if self._native_recording:
            return "break"
        try:
            shortcut = self.view.on_key_press(event)
        except MalformedTkEvent:
            return "break"
        except EngineUnhealthy as exc:
            self._cleanup_recording_state()
            self._handle_engine_unhealthy(exc)
            return "break"
        except Exception as exc:
            self._cleanup_recording_state()
            self._show_error(exc)
            return "break"
        self.hotkey_var.set(self.view.hotkey_text)
        if shortcut is not None:
            self.status_var.set("Shortcut captured. Save to apply it.")
            self._unbind_recording_events()
        self._sync_controls()
        return "break"

    def _on_key_release(self, event) -> str:
        if self._native_recording:
            return "break"
        try:
            self.view.on_key_release(event)
        except MalformedTkEvent:
            return "break"
        except EngineUnhealthy as exc:
            self._cleanup_recording_state()
            self._handle_engine_unhealthy(exc)
            return "break"
        except Exception as exc:
            self._cleanup_recording_state()
            self._show_error(exc)
            return "break"
        self.hotkey_var.set(self.view.hotkey_text)
        self._sync_controls()
        return "break"

    def _on_focus_out(self, _event=None) -> None:
        try:
            self.view.on_focus_out()
        except EngineUnhealthy as exc:
            self._cleanup_recording_state()
            self._handle_engine_unhealthy(exc)
        except Exception as exc:
            self._cleanup_recording_state()
            self._show_error(exc)
        else:
            self._cleanup_recording_state()

    def _bind_recording_events(self) -> None:
        if self._recording_bindings:
            return
        binding_specs = (
            (self.hotkey_entry, "<KeyPress>", self._on_key_press),
            (self.window, "<KeyPress>", self._on_key_press),
            (self.hotkey_entry, "<KeyRelease>", self._on_key_release),
            (self.window, "<KeyRelease>", self._on_key_release),
            (self.hotkey_entry, "<FocusOut>", self._on_focus_out),
        )
        try:
            for widget, sequence, callback in binding_specs:
                binding_id = widget.bind(sequence, callback)
                self._recording_bindings.append((widget, sequence, binding_id))
        except Exception:
            self._unbind_recording_events()
            raise

    def _unbind_recording_events(self) -> None:
        for widget, sequence, binding_id in self._recording_bindings:
            widget.unbind(sequence, binding_id)
        self._recording_bindings.clear()

    def _cleanup_recording_state(self) -> None:
        self._cancel_recording_poll()
        try:
            self.view.cancel_recording()
        except Exception:
            pass
        self._unbind_recording_events()
        self.hotkey_var.set(self.view.hotkey_text)
        self._sync_controls()

    def _handle_engine_unhealthy(self, error: EngineUnhealthy) -> None:
        if self._on_engine_unhealthy is not None:
            self._on_engine_unhealthy(error)
            return
        self._show_error(error)

    def _sync_controls(self) -> None:
        for action, (variable, entry, button) in getattr(self, "_shortcut_rows", {}).items():
            entry.configure(state="disabled" if self.view.locked else "readonly")
            button.configure(text=f"Record {action} shortcut", state="normal" if self.view.record_enabled else "disabled")
            if action != self.view.action:
                variable.set(self.view._display_shortcut(parse_shortcut(self.view._drafts[action])))
        self.record_button.configure(
            text="Press a shortcut..." if self.view.recording else f"Record {self.view.action} shortcut",
            state="normal" if self.view.record_enabled else "disabled",
        )
        self.save_button.configure(
            state="normal" if self.view.save_enabled else "disabled",
        )
        self.hotkey_entry.configure(
            state="disabled" if self.view.locked else "readonly",
        )

    def _show_error(self, error: BaseException) -> None:
        self._messagebox.showerror("CatLocker Settings", str(error), parent=self.window)
