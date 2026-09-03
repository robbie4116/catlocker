from pathlib import Path

import pytest

from hotkeys import ShortcutError
from settings import AppSettings, load_settings, resolve_config_path, save_settings


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
