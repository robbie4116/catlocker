import json
from pathlib import Path

import pytest

from hotkeys import ShortcutError, ShortcutPair, parse_shortcut
from settings import (
    RUN_KEY,
    STARTUP_VALUE,
    AppSettings,
    StartupRegistry,
    build_startup_command,
    encode_settings,
    load_settings,
    resolve_config_path,
    save_settings,
)


def _toml_literal(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    return repr(value)


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
    assert settings.separate_shortcuts is False
    assert settings.active_shortcuts() == ShortcutPair(parse_shortcut("F24"), parse_shortcut("F24"))
    assert path.exists()


def test_partial_config_merges_defaults_and_normalizes_hotkey(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('toggle_hotkey = "shift+ctrl+k"\n', encoding="utf-8")
    assert load_settings(path) == AppSettings("Ctrl+Shift+K", True)


def test_invalid_hotkey_falls_back_to_f24_without_losing_notification_choice(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('toggle_hotkey = "ctrl"\nnotifications = false\n', encoding="utf-8")
    assert load_settings(path) == AppSettings("F24", False)


# --- Shortcut-mode migration precedence -------------------------------------------------

MIGRATION_CASES = [
    pytest.param("", False, "F24", "F24", "F24", id="fresh"),
    pytest.param('toggle_hotkey = "F12"\n', False, "F12", "F12", "F12", id="legacy-toggle-only"),
    pytest.param(
        'lock_hotkey = "F23"\nunlock_hotkey = "F23"\n',
        False, "F23", "F23", "F23",
        id="equal-pair",
    ),
    pytest.param(
        'lock_hotkey = "F23"\nunlock_hotkey = "F24"\n',
        True, "F23", "F23", "F24",
        id="distinct-pair",
    ),
    pytest.param(
        'toggle_hotkey = "F12"\nlock_hotkey = "F24"\nunlock_hotkey = "F24"\n',
        False, "F24", "F24", "F24",
        id="equal-pair-plus-legacy-toggle-no-flag",
    ),
    pytest.param(
        'separate_shortcuts = false\ntoggle_hotkey = "F12"\nlock_hotkey = "F23"\nunlock_hotkey = "F24"\n',
        False, "F12", "F23", "F24",
        id="explicit-false-toggle-and-distinct-pair",
    ),
    pytest.param(
        'separate_shortcuts = true\ntoggle_hotkey = "F12"\nlock_hotkey = "F23"\n',
        True, "F12", "F23", "F12",
        id="explicit-true-toggle-and-lock-only",
    ),
    pytest.param(
        'separate_shortcuts = 1\nlock_hotkey = "F23"\nunlock_hotkey = "F24"\n',
        True, "F23", "F23", "F24",
        id="invalid-flag-with-distinct-pair",
    ),
]


@pytest.mark.parametrize(
    "content, expected_separate, expected_toggle, expected_lock, expected_unlock",
    MIGRATION_CASES,
)
def test_migration_precedence_table(
    tmp_path, content, expected_separate, expected_toggle, expected_lock, expected_unlock
):
    path = tmp_path / "config.toml"
    path.write_text(content, encoding="utf-8")
    original_bytes = path.read_bytes()

    settings = load_settings(path)

    assert settings.separate_shortcuts is expected_separate
    assert settings.toggle_hotkey == expected_toggle
    assert settings.lock_hotkey == expected_lock
    assert settings.unlock_hotkey == expected_unlock

    expected_pair = (
        ShortcutPair(parse_shortcut(expected_lock), parse_shortcut(expected_unlock))
        if expected_separate
        else ShortcutPair(parse_shortcut(expected_toggle), parse_shortcut(expected_toggle))
    )
    assert settings.active_shortcuts() == expected_pair

    # Migration alone must never rewrite an otherwise readable file.
    assert path.read_bytes() == original_bytes


def test_invalid_lock_falls_back_to_toggle_then_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('lock_hotkey = "NotAKey"\nunlock_hotkey = "F23"\n', encoding="utf-8")
    settings = load_settings(path)
    assert settings.lock_hotkey == "F24"
    assert settings.unlock_hotkey == "F23"
    assert settings.toggle_hotkey == "F24"
    assert settings.separate_shortcuts is True


def test_missing_unlock_falls_back_to_default_and_infers_separate_mode(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('lock_hotkey = "F23"\n', encoding="utf-8")
    settings = load_settings(path)
    assert settings.lock_hotkey == "F23"
    assert settings.unlock_hotkey == "F24"
    assert settings.toggle_hotkey == "F23"
    assert settings.separate_shortcuts is True


def test_explicit_separate_mode_survives_equal_resolved_pair_from_invalid_toggle(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('separate_shortcuts = true\ntoggle_hotkey = "bogus"\n', encoding="utf-8")
    settings = load_settings(path)
    assert settings.lock_hotkey == "F24"
    assert settings.unlock_hotkey == "F24"
    assert settings.toggle_hotkey == "F24"
    assert settings.separate_shortcuts is True


@pytest.mark.parametrize("bad_flag", [1, 0, "true", "false", 1.5])
def test_invalid_mode_flag_types_fall_back_to_pair_inference(tmp_path, bad_flag):
    path = tmp_path / "config.toml"
    literal = _toml_literal(bad_flag)
    path.write_text(
        f'separate_shortcuts = {literal}\nlock_hotkey = "F23"\nunlock_hotkey = "F24"\n',
        encoding="utf-8",
    )
    settings = load_settings(path)
    assert settings.separate_shortcuts is True
    assert settings.toggle_hotkey == "F23"
    assert settings.lock_hotkey == "F23"
    assert settings.unlock_hotkey == "F24"


@pytest.mark.parametrize("bad_notifications", [1, 0, "true", "yes"])
def test_invalid_notifications_type_falls_back_to_default(tmp_path, bad_notifications):
    path = tmp_path / "config.toml"
    literal = _toml_literal(bad_notifications)
    path.write_text(f"notifications = {literal}\n", encoding="utf-8")
    settings = load_settings(path)
    assert settings.notifications is True


def test_generic_and_specific_modifiers_preserved_across_all_three_fields(tmp_path):
    path = tmp_path / "config.toml"
    settings = AppSettings(
        toggle_hotkey="Ctrl+F13",
        lock_hotkey="LCtrl+F14",
        unlock_hotkey="RCtrl+F15",
        separate_shortcuts=True,
    )
    save_settings(path, settings)

    loaded = load_settings(path)

    assert loaded.toggle_hotkey == "Ctrl+F13"
    assert loaded.lock_hotkey == "LCtrl+F14"
    assert loaded.unlock_hotkey == "RCtrl+F15"


def test_round_trip_preserves_inactive_bindings_and_active_pair(tmp_path):
    path = tmp_path / "config.toml"
    single = AppSettings(
        toggle_hotkey="F12",
        lock_hotkey="F23",
        unlock_hotkey="F24",
        separate_shortcuts=False,
    )
    save_settings(path, single)
    loaded_single = load_settings(path)
    assert loaded_single == single
    assert loaded_single.active_shortcuts() == ShortcutPair(parse_shortcut("F12"), parse_shortcut("F12"))

    separate = AppSettings(
        toggle_hotkey="F12",
        lock_hotkey="F23",
        unlock_hotkey="F24",
        separate_shortcuts=True,
    )
    save_settings(path, separate)
    loaded_separate = load_settings(path)
    assert loaded_separate == separate
    assert loaded_separate.active_shortcuts() == ShortcutPair(parse_shortcut("F23"), parse_shortcut("F24"))


def test_encode_settings_serializes_all_fields():
    settings = AppSettings(
        toggle_hotkey="F12",
        lock_hotkey="F23",
        unlock_hotkey="F24",
        separate_shortcuts=True,
        notifications=False,
    )
    text = encode_settings(settings)
    assert 'separate_shortcuts = true' in text
    assert 'toggle_hotkey = "F12"' in text
    assert 'lock_hotkey = "F23"' in text
    assert 'unlock_hotkey = "F24"' in text
    assert 'notifications = false' in text


def test_positional_construction_defaults_to_single_mode():
    settings = AppSettings("F12", True)
    assert settings.separate_shortcuts is False
    assert settings.toggle_hotkey == settings.lock_hotkey == settings.unlock_hotkey == "F12"


def test_pair_only_construction_infers_mode_from_equality():
    equal_pair = AppSettings(lock_hotkey="F23", unlock_hotkey="F23")
    assert equal_pair.separate_shortcuts is False
    assert equal_pair.toggle_hotkey == "F23"

    distinct_pair = AppSettings(lock_hotkey="F23", unlock_hotkey="F24")
    assert distinct_pair.separate_shortcuts is True
    assert distinct_pair.toggle_hotkey == "F23"


def test_explicit_mode_overrides_pair_inference():
    settings = AppSettings(lock_hotkey="F23", unlock_hotkey="F23", separate_shortcuts=True)
    assert settings.separate_shortcuts is True


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


@pytest.mark.parametrize("hotkey", ["RAlt", "LCtrl+OEM_3", "Ctrl+Shift+K"])
def test_new_and_legacy_shortcuts_round_trip_without_rewriting_tokens(tmp_path, hotkey):
    path = tmp_path / "config.toml"

    save_settings(path, AppSettings(hotkey, False))

    assert load_settings(path) == AppSettings(hotkey, False)


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
