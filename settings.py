from __future__ import annotations

import os
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hotkeys import ShortcutError, ShortcutPair, parse_shortcut, validate_shortcut


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE = "CatLocker"

_UNSET = object()


@dataclass(frozen=True, slots=True, init=False)
class AppSettings:
    separate_shortcuts: bool = False
    toggle_hotkey: str = "F24"
    lock_hotkey: str = "F24"
    unlock_hotkey: str = "F24"
    notifications: bool = True

    def __init__(
        self,
        toggle_hotkey=_UNSET,
        notifications=True,
        *,
        lock_hotkey=None,
        unlock_hotkey=None,
        separate_shortcuts=None,
    ):
        """Construct settings.

        Preserves two legacy call shapes alongside the new explicit-mode one:

        - Old positional toggle/notifications construction, e.g. ``AppSettings("F12", True)``:
          no pair is supplied, so mode defaults to single and lock/unlock are seeded from toggle.
        - Old pair-only keyword construction, e.g. ``AppSettings(lock_hotkey=..., unlock_hotkey=...)``:
          with no explicit mode, mode is inferred from whether the pair is equal, and toggle is
          seeded from lock (matching the file-migration rules for equal/distinct pairs).

        An explicitly supplied ``separate_shortcuts`` always wins over inference.
        """
        pair_supplied = lock_hotkey is not None or unlock_hotkey is not None
        toggle_supplied = toggle_hotkey is not _UNSET

        if pair_supplied:
            fallback = toggle_hotkey if toggle_supplied else "F24"
            resolved_lock = lock_hotkey if lock_hotkey is not None else fallback
            resolved_unlock = unlock_hotkey if unlock_hotkey is not None else fallback
        else:
            resolved_lock = resolved_unlock = toggle_hotkey if toggle_supplied else "F24"

        if separate_shortcuts is not None:
            resolved_separate = bool(separate_shortcuts)
        elif pair_supplied:
            resolved_separate = resolved_lock != resolved_unlock
        else:
            resolved_separate = False

        resolved_toggle = toggle_hotkey if toggle_supplied else resolved_lock

        object.__setattr__(self, "separate_shortcuts", resolved_separate)
        object.__setattr__(self, "toggle_hotkey", resolved_toggle)
        object.__setattr__(self, "lock_hotkey", resolved_lock)
        object.__setattr__(self, "unlock_hotkey", resolved_unlock)
        object.__setattr__(self, "notifications", notifications)

    def active_shortcuts(self) -> ShortcutPair:
        """The pair that should actually be installed: lock/unlock when separate, toggle/toggle otherwise.

        Centralizes selection so application startup and Settings Save cannot disagree.
        """
        if self.separate_shortcuts:
            return ShortcutPair(parse_shortcut(self.lock_hotkey), parse_shortcut(self.unlock_hotkey))
        return ShortcutPair(parse_shortcut(self.toggle_hotkey), parse_shortcut(self.toggle_hotkey))


def build_startup_command(
    executable: Path | str,
    script: Path | str | None = None,
) -> str:
    def quote_path(path: Path | str) -> str:
        value = str(path).replace('"', '\\"')
        return f'"{value}"'

    arguments = [quote_path(executable)]
    if script is not None:
        arguments.append(quote_path(script))
    arguments.append("--startup")
    return " ".join(arguments)


class WindowsRegistryAdapter:
    def get_value(self, key: str, name: str) -> str:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            key,
            0,
            winreg.KEY_READ,
        ) as handle:
            value, _ = winreg.QueryValueEx(handle, name)
            return value

    def set_value(self, key: str, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            key,
            0,
            winreg.KEY_SET_VALUE,
        ) as handle:
            winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, value)

    def delete_value(self, key: str, name: str) -> None:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            key,
            0,
            winreg.KEY_SET_VALUE,
        ) as handle:
            winreg.DeleteValue(handle, name)


class StartupRegistry:
    def __init__(self, registry: WindowsRegistryAdapter, command: str):
        self._registry = registry
        self.command = command

    def is_enabled(self) -> bool:
        try:
            actual_command = self._registry.get_value(RUN_KEY, STARTUP_VALUE)
        except FileNotFoundError:
            return False
        return actual_command == self.command

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._registry.set_value(RUN_KEY, STARTUP_VALUE, self.command)
            return
        try:
            self._registry.delete_value(RUN_KEY, STARTUP_VALUE)
        except FileNotFoundError:
            pass


def resolve_config_path(executable_dir: Path, local_appdata: Path) -> Path:
    portable = executable_dir / "catlocker.toml"
    return portable if portable.is_file() else local_appdata / "CatLocker" / "config.toml"


def encode_settings(settings: AppSettings) -> str:
    import json
    separate_shortcuts = "true" if settings.separate_shortcuts else "false"
    notifications = "true" if settings.notifications else "false"
    return (f'separate_shortcuts = {separate_shortcuts}\n'
            f'toggle_hotkey = {json.dumps(settings.toggle_hotkey)}\n'
            f'lock_hotkey = {json.dumps(settings.lock_hotkey)}\n'
            f'unlock_hotkey = {json.dumps(settings.unlock_hotkey)}\n'
            f'notifications = {notifications}\n')


def _canonicalize_hotkey(raw: str) -> str:
    if not isinstance(raw, str):
        raise ShortcutError("A shortcut must be text.")
    return validate_shortcut(parse_shortcut(raw)).shortcut.canonical


def _canonicalize_settings(settings: AppSettings) -> AppSettings:
    return AppSettings(
        toggle_hotkey=_canonicalize_hotkey(settings.toggle_hotkey),
        lock_hotkey=_canonicalize_hotkey(settings.lock_hotkey),
        unlock_hotkey=_canonicalize_hotkey(settings.unlock_hotkey),
        separate_shortcuts=settings.separate_shortcuts,
        notifications=settings.notifications,
    )


def _corrupt_backup_path(path: Path) -> Path:
    candidate = path.with_name(path.name + ".corrupt")
    number = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt.{number}")
        number += 1
    return candidate


def _preserve_corrupt(path: Path) -> None:
    os.replace(path, _corrupt_backup_path(path))


def _canonical_or_none(raw: object) -> str | None:
    """A shortcut value resolved for use, or None if missing or invalid ("unavailable")."""
    if not isinstance(raw, str):
        return None
    try:
        return _canonicalize_hotkey(raw)
    except ShortcutError:
        return None


def load_settings(path: Path) -> AppSettings:
    path = Path(path)
    if not path.exists():
        save_settings(path, AppSettings())
        return AppSettings()

    try:
        with path.open("rb") as handle:
            loaded = tomllib.load(handle)
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        _preserve_corrupt(path)
        save_settings(path, AppSettings())
        return AppSettings()

    data = loaded if isinstance(loaded, dict) else {}
    defaults = AppSettings()

    valid_toggle = _canonical_or_none(data.get("toggle_hotkey"))
    valid_lock = _canonical_or_none(data.get("lock_hotkey"))
    valid_unlock = _canonical_or_none(data.get("unlock_hotkey"))

    # Resolve lock/unlock independently of mode: lock_hotkey/unlock_hotkey win when valid,
    # falling back to toggle_hotkey, then F24. This is what lets remembered inactive bindings
    # survive even when they don't match the active mode.
    resolved_lock = valid_lock if valid_lock is not None else (
        valid_toggle if valid_toggle is not None else defaults.lock_hotkey
    )
    resolved_unlock = valid_unlock if valid_unlock is not None else (
        valid_toggle if valid_toggle is not None else defaults.unlock_hotkey
    )

    raw_mode = data.get("separate_shortcuts")
    if isinstance(raw_mode, bool):
        # A valid explicit mode flag always wins; toggle is resolved independently,
        # falling back to the resolved lock binding.
        resolved_toggle = valid_toggle if valid_toggle is not None else resolved_lock
        resolved_separate = raw_mode
    else:
        # No valid mode flag: seed toggle from the resolved lock binding and infer mode
        # from whether the resolved pair differs, preserving older mixed files' active binding.
        resolved_toggle = resolved_lock
        resolved_separate = resolved_lock != resolved_unlock

    resolved_notifications = defaults.notifications
    raw_notifications = data.get("notifications")
    if isinstance(raw_notifications, bool):
        resolved_notifications = raw_notifications

    return AppSettings(
        toggle_hotkey=resolved_toggle,
        lock_hotkey=resolved_lock,
        unlock_hotkey=resolved_unlock,
        separate_shortcuts=resolved_separate,
        notifications=resolved_notifications,
    )


def save_settings(
    path: Path,
    settings: AppSettings,
    *,
    replace=os.replace,
    unlink=os.unlink,
) -> None:
    path = Path(path)
    canonical = _canonicalize_settings(settings)
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".catlocker-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            handle.write(encode_settings(canonical))
            handle.flush()
            os.fsync(handle.fileno())
        replace(temp_path, path)
    finally:
        if temp_path is not None and os.path.exists(temp_path):
            try:
                unlink(temp_path)
            except OSError:
                pass
