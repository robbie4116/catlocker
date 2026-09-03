from __future__ import annotations

import os
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from hotkeys import ShortcutError, parse_shortcut, validate_shortcut


@dataclass(frozen=True, slots=True)
class AppSettings:
    toggle_hotkey: str = "F24"
    notifications: bool = True


def resolve_config_path(executable_dir: Path, local_appdata: Path) -> Path:
    portable = executable_dir / "catlocker.toml"
    return portable if portable.is_file() else local_appdata / "CatLocker" / "config.toml"


def encode_settings(settings: AppSettings) -> str:
    hotkey = settings.toggle_hotkey.replace("\\", "\\\\").replace('"', '\\"')
    notifications = "true" if settings.notifications else "false"
    return f'toggle_hotkey = "{hotkey}"\nnotifications = {notifications}\n'


def _canonicalize_hotkey(raw: str) -> str:
    if not isinstance(raw, str):
        raise ShortcutError("A shortcut must be text.")
    return validate_shortcut(parse_shortcut(raw)).shortcut.canonical


def _canonicalize_settings(settings: AppSettings) -> AppSettings:
    return AppSettings(
        toggle_hotkey=_canonicalize_hotkey(settings.toggle_hotkey),
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

    return AppSettings(toggle_hotkey=hotkey, notifications=notifications)


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
