from pathlib import Path

import pytest

from hotkeys import ShortcutError
from settings import (
    RUN_KEY,
    STARTUP_VALUE,
    AppSettings,
    StartupRegistry,
    build_startup_command,
    load_settings,
    resolve_config_path,
    save_settings,
)


def test_installed_mode_uses_local_appdata(tmp_path):
    exe_dir = tmp_path / "install"
    exe_dir.mkdir()
    path = resolve_config_path(exe_dir, tmp_path / "local")
    assert path == tmp_path / "local" / "CatLocker" / "config.toml"


def test_existing_beside_executable_file_enables_portable_mode(tmp_path):
    portable = tmp_path / "catlocker.toml"
    portable.write_text('toggle_hotkey = "F24"\nnotifications = true\n', encoding="utf-8")
    assert resolve_config_path(tmp_path, tmp_path / "local") == portable


def test_missing_config_is_created_with_safe_defaults(tmp_path):
    path = tmp_path / "config.toml"
    settings = load_settings(path)
    assert settings == AppSettings(toggle_hotkey="F24", notifications=True)
    assert path.exists()


def test_partial_config_merges_defaults_and_normalizes_hotkey(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('toggle_hotkey = "shift+ctrl+k"\n', encoding="utf-8")
    assert load_settings(path) == AppSettings("Ctrl+Shift+K", True)


def test_invalid_hotkey_falls_back_to_f24_without_losing_notification_choice(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('toggle_hotkey = "ctrl"\nnotifications = false\n', encoding="utf-8")
    assert load_settings(path) == AppSettings("F24", False)


def test_corrupt_toml_is_preserved_before_defaults_are_written(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("not = [valid", encoding="utf-8")
    assert load_settings(path) == AppSettings()
    assert (tmp_path / "config.toml.corrupt").read_text(encoding="utf-8") == "not = [valid"
    assert load_settings(path) == AppSettings()


def test_invalid_utf8_is_preserved_as_corrupt_configuration(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes(b"\xff\xfe")
    assert load_settings(path) == AppSettings()
    assert (tmp_path / "config.toml.corrupt").read_bytes() == b"\xff\xfe"


def test_corrupt_backup_uses_first_free_numbered_name(tmp_path):
    path = tmp_path / "config.toml"
    (tmp_path / "config.toml.corrupt").write_text("older", encoding="utf-8")
    path.write_text("not = [valid", encoding="utf-8")
    load_settings(path)
    assert (tmp_path / "config.toml.corrupt").read_text(encoding="utf-8") == "older"
    assert (tmp_path / "config.toml.corrupt.1").read_text(encoding="utf-8") == "not = [valid"
    assert load_settings(path) == AppSettings()


def test_atomic_save_round_trips_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings("Ctrl+Alt+F12", False))
    assert load_settings(path) == AppSettings("Ctrl+Alt+F12", False)
    assert list(tmp_path.glob(".catlocker-*.tmp")) == []


def test_replace_failure_preserves_previous_file_and_cleans_temp(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings())
    original = path.read_bytes()

    def fail_replace(source, destination):
        raise OSError("disk full")

    with pytest.raises(OSError, match="disk full"):
        save_settings(path, AppSettings("K", False), replace=fail_replace)
    assert path.read_bytes() == original
    assert list(tmp_path.glob(".catlocker-*.tmp")) == []


def test_invalid_shortcut_never_touches_existing_file(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings())
    original = path.read_bytes()
    with pytest.raises(ShortcutError):
        save_settings(path, AppSettings("Ctrl", False))
    assert path.read_bytes() == original


def test_cleanup_failure_does_not_mask_replace_failure(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings())

    def fail_replace(source, destination):
        raise OSError("disk full")

    def fail_unlink(path):
        raise PermissionError("cleanup denied")

    with pytest.raises(OSError, match="disk full"):
        save_settings(
            path,
            AppSettings("K", False),
            replace=fail_replace,
            unlink=fail_unlink,
        )


class FakeRegistry:
    def __init__(self, *, read_error=None, write_error=None, delete_error=None):
        self.values = {}
        self.read_error = read_error
        self.write_error = write_error
        self.delete_error = delete_error

    def get_value(self, key, name):
        if self.read_error is not None:
            raise self.read_error
        try:
            return self.values[(key, name)]
        except KeyError:
            raise FileNotFoundError(name) from None

    def set_value(self, key, name, value):
        if self.write_error is not None:
            raise self.write_error
        self.values[(key, name)] = value

    def delete_value(self, key, name):
        if self.delete_error is not None:
            raise self.delete_error
        try:
            del self.values[(key, name)]
        except KeyError:
            raise FileNotFoundError(name) from None


def test_frozen_startup_command_quotes_executable_path():
    command = build_startup_command(
        executable=Path(r"C:\Program Files\CatLocker\catlocker.exe"),
        script=None,
    )
    assert command == '"C:\\Program Files\\CatLocker\\catlocker.exe" --startup'


def test_script_startup_command_uses_pythonw_and_quotes_both_paths():
    command = build_startup_command(
        executable=Path(r"C:\Program Files\Python\pythonw.exe"),
        script=Path(r"D:\My Apps\catlocker\main.py"),
    )
    assert command == '"C:\\Program Files\\Python\\pythonw.exe" "D:\\My Apps\\catlocker\\main.py" --startup'


def test_startup_state_is_derived_from_actual_registry_value():
    fake = FakeRegistry()
    startup = StartupRegistry(fake, '"C:\\CatLocker\\catlocker.exe" --startup')
    assert startup.is_enabled() is False
    startup.set_enabled(True)
    assert fake.values[(RUN_KEY, STARTUP_VALUE)] == startup.command
    assert startup.is_enabled() is True
    fake.values[(RUN_KEY, STARTUP_VALUE)] = "different command"
    assert startup.is_enabled() is False
    startup.set_enabled(False)
    assert (RUN_KEY, STARTUP_VALUE) not in fake.values


def test_registry_failure_does_not_modify_toml(tmp_path):
    path = tmp_path / "config.toml"
    save_settings(path, AppSettings("F24", False))
    original = path.read_bytes()
    startup = StartupRegistry(FakeRegistry(write_error=PermissionError("denied")), "command")
    with pytest.raises(PermissionError, match="denied"):
        startup.set_enabled(True)
    assert path.read_bytes() == original


def test_disabling_missing_startup_value_is_idempotent():
    startup = StartupRegistry(FakeRegistry(), "command")
    startup.set_enabled(False)
    assert startup.is_enabled() is False


def test_registry_read_and_delete_errors_propagate():
    with pytest.raises(PermissionError, match="read denied"):
        StartupRegistry(FakeRegistry(read_error=PermissionError("read denied")), "command").is_enabled()
    with pytest.raises(PermissionError, match="delete denied"):
        StartupRegistry(FakeRegistry(delete_error=PermissionError("delete denied")), "command").set_enabled(False)
