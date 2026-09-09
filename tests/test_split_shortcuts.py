from hotkeys import (
    InputState,
    KeyEvent,
    ShortcutPair,
    VK_LCONTROL,
    VK_RCONTROL,
    parse_shortcut,
)
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


# --- Task 5: end-to-end proof that AppSettings.active_shortcuts() drives InputState
# identically to a hand-built ShortcutPair. These do not exercise new engine behavior;
# they prove the Task 2 selector is wired correctly into the existing, already-tested
# InputState toggle/state-dependent/standalone-modifier/emergency semantics.


def test_active_shortcuts_single_mode_shared_trigger_toggles_once_then_back():
    settings = AppSettings(toggle_hotkey='F24', separate_shortcuts=False)
    pair = settings.active_shortcuts()
    assert pair.lock.canonical == pair.unlock.canonical == 'F24'

    state = InputState(pair)
    F24 = 0x87

    first_down = state.handle(KeyEvent(vk=F24, is_keydown=True))
    assert first_down.locked is True
    assert first_down.changed is True

    # Autorepeat keydowns while the trigger is still held must not retoggle.
    repeat = state.handle(KeyEvent(vk=F24, is_keydown=True))
    assert repeat.changed is False
    assert repeat.locked is True
    repeat_again = state.handle(KeyEvent(vk=F24, is_keydown=True))
    assert repeat_again.changed is False
    assert repeat_again.locked is True

    state.handle(KeyEvent(vk=F24, is_keydown=False))
    assert state.locked is True

    # A fresh press toggles back.
    second_down = state.handle(KeyEvent(vk=F24, is_keydown=True))
    assert second_down.changed is True
    assert second_down.locked is False
    state.handle(KeyEvent(vk=F24, is_keydown=False))


def test_active_shortcuts_single_mode_standalone_modifier_toggles_on_release_only():
    settings = AppSettings(toggle_hotkey='RAlt', separate_shortcuts=False)
    pair = settings.active_shortcuts()
    assert pair.lock.canonical == pair.unlock.canonical == 'RAlt'

    state = InputState(pair)
    vk = pair.lock.trigger_vk

    down_result = state.handle(KeyEvent(vk=vk, is_keydown=True))
    assert down_result.changed is False
    assert down_result.locked is False

    up_result = state.handle(KeyEvent(vk=vk, is_keydown=False))
    assert up_result.changed is True
    assert up_result.reason == 'toggle'
    assert up_result.locked is True

    # Toggling back: press and release again.
    state.handle(KeyEvent(vk=vk, is_keydown=True))
    back = state.handle(KeyEvent(vk=vk, is_keydown=False))
    assert back.changed is True
    assert back.locked is False


def test_active_shortcuts_separate_mode_remains_state_dependent():
    settings = AppSettings(lock_hotkey='F23', unlock_hotkey='F24', separate_shortcuts=True)
    pair = settings.active_shortcuts()
    assert pair.lock.canonical == 'F23'
    assert pair.unlock.canonical == 'F24'

    state = InputState(pair)
    LOCK, UNLOCK = 0x86, 0x87

    assert press(state, LOCK).locked is True
    # Pressing lock again while already locked does nothing (not a toggle).
    assert not state.handle(KeyEvent(vk=LOCK, is_keydown=True)).changed
    state.handle(KeyEvent(vk=LOCK, is_keydown=False))
    assert state.locked is True

    assert press(state, UNLOCK).changed is True
    assert state.locked is False
    # Pressing unlock again while already unlocked does nothing.
    assert not state.handle(KeyEvent(vk=UNLOCK, is_keydown=True)).changed
    state.handle(KeyEvent(vk=UNLOCK, is_keydown=False))
    assert state.locked is False


def test_active_shortcuts_emergency_recovery_in_single_mode():
    settings = AppSettings(toggle_hotkey='F24', separate_shortcuts=False)
    state = InputState(settings.active_shortcuts(), locked=True)

    state.handle(KeyEvent(vk=VK_LCONTROL, is_keydown=True))
    result = state.handle(KeyEvent(vk=VK_RCONTROL, is_keydown=True))

    assert result.reason == 'emergency'
    assert result.locked is False
    assert state.locked is False


def test_active_shortcuts_emergency_recovery_in_separate_mode():
    settings = AppSettings(lock_hotkey='F23', unlock_hotkey='F24', separate_shortcuts=True)
    state = InputState(settings.active_shortcuts(), locked=True)

    state.handle(KeyEvent(vk=VK_LCONTROL, is_keydown=True))
    result = state.handle(KeyEvent(vk=VK_RCONTROL, is_keydown=True))

    assert result.reason == 'emergency'
    assert result.locked is False
    assert state.locked is False
