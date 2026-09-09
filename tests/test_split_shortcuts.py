from hotkeys import InputState, KeyEvent, ShortcutPair, parse_shortcut
from settings import AppSettings, load_settings, save_settings


def press(state, vk):
    result = state.handle(KeyEvent(vk=vk, is_keydown=True))
    state.handle(KeyEvent(vk=vk, is_keydown=False))
    return result


def test_distinct_actions_and_repeats():
    state = InputState(ShortcutPair(parse_shortcut('F23'), parse_shortcut('F24')))
    assert not press(state, 0x87).locked
    assert press(state, 0x86).locked
    assert not state.handle(KeyEvent(vk=0x86, is_keydown=True)).changed
    state.handle(KeyEvent(vk=0x86, is_keydown=False))
    assert press(state, 0x87).changed
    assert not state.locked


def test_shared_shortcut_toggles_without_repeat():
    key = parse_shortcut('F24')
    state = InputState(ShortcutPair(key, key))
    assert state.handle(KeyEvent(vk=0x87, is_keydown=True)).locked
    assert not state.handle(KeyEvent(vk=0x87, is_keydown=True)).changed
    state.handle(KeyEvent(vk=0x87, is_keydown=False))
    assert not press(state, 0x87).locked


def test_settings_migration_and_roundtrip(tmp_path):
    path = tmp_path / 'config.toml'
    path.write_text('toggle_hotkey = "Ctrl+F12"\n')
    migrated = load_settings(path)
    assert migrated.lock_hotkey == migrated.unlock_hotkey == 'Ctrl+F12'
    assert migrated.toggle_hotkey == 'Ctrl+F12'
    assert migrated.separate_shortcuts is False

    settings = AppSettings(lock_hotkey='F23', unlock_hotkey='F24')
    assert settings.separate_shortcuts is True
    assert settings.toggle_hotkey == 'F23'
    save_settings(path, settings)
    reloaded = load_settings(path)
    assert reloaded == settings
    # All remembered values -- mode, toggle, and both pair members -- survive the round trip.
    text = path.read_text()
    assert 'toggle_hotkey' in text
    assert 'separate_shortcuts' in text
    assert 'lock_hotkey' in text
    assert 'unlock_hotkey' in text


def test_unlock_while_lock_trigger_is_still_held():
    state = InputState(ShortcutPair(parse_shortcut('F23'), parse_shortcut('F24')))
    assert state.handle(KeyEvent(vk=0x86, is_keydown=True)).locked
    assert not state.handle(KeyEvent(vk=0x87, is_keydown=True)).locked


def test_distinct_standalone_modifiers():
    state = InputState(ShortcutPair(parse_shortcut('LShift'), parse_shortcut('RAlt')))
    press(state, 0xA0)
    assert state.locked
    press(state, 0xA0)
    assert state.locked
    press(state, 0xA5)
    assert not state.locked
