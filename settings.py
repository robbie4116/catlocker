from __future__ import annotations

import os
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hotkeys import ShortcutError, parse_shortcut, validate_shortcut


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_VALUE = "CatLocker"


@dataclass(frozen=True, slots=True, init=False)
class AppSettings:
    lock_hotkey: str = "F24"
    unlock_hotkey: str = "F24"
    notifications: bool = True

    def __init__(self, toggle_hotkey="F24", notifications=True, *, lock_hotkey=None, unlock_hotkey=None):
        object.__setattr__(self, "lock_hotkey", toggle_hotkey if lock_hotkey is None else lock_hotkey)
        object.__setattr__(self, "unlock_hotkey", toggle_hotkey if unlock_hotkey is None else unlock_hotkey)
        object.__setattr__(self, "notifications", notifications)

    @property
    def toggle_hotkey(self):
        """Compatibility alias for callers using the old single-shortcut API."""
        return self.lock_hotkey


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
    notifications = "true" if settings.notifications else "false"
    return (f'lock_hotkey = {json.dumps(settings.lock_hotkey)}\n'
            f'unlock_hotkey = {json.dumps(settings.unlock_hotkey)}\n'
            f'notifications = {notifications}\n')


def _canonicalize_hotkey(raw: str) -> str:
    if not isinstance(raw, str):
        raise ShortcutError("A shortcut must be text.")
    return validate_shortcut(parse_shortcut(raw)).shortcut.canonical


def _canonicalize_settings(settings: AppSettings) -> AppSettings:
    return AppSettings(
        lock_hotkey=_canonicalize_hotkey(settings.lock_hotkey),
        unlock_hotkey=_canonicalize_hotkey(settings.unlock_hotkey),
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

    hotkey = AppSettings().toggle_hotkey
    raw_hotkey = loaded.get("toggle_hotkey") if isinstance(loaded, dict) else None
    if isinstance(raw_hotkey, str):
        try:
            hotkey = _canonicalize_hotkey(raw_hotkey)
        except ShortcutError:
            pass

    notifications = AppSettings().notifications
    raw_notifications = loaded.get("notifications") if isinstance(loaded, dict) else None
    if isinstance(raw_notifications, bool):
        notifications = raw_notifications

    def read_hotkey(name):
        try:
            return _canonicalize_hotkey(loaded.get(name, hotkey))
        except ShortcutError:
            return hotkey

    return AppSettings(lock_hotkey=read_hotkey("lock_hotkey"),
                       unlock_hotkey=read_hotkey("unlock_hotkey"),
                       notifications=notifications)


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
