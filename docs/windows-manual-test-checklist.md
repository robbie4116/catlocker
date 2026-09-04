# CatLocker Windows Manual Test Checklist

Use this checklist to record manual verification on Windows 11. Mark each item and fill in the environment and results fields. If a required tool is unavailable, leave the item explicitly pending instead of guessing.

## Environment

- Date:
- Windows edition:
- Windows build:
- Python version:
- PyInstaller version:
- Inno Setup version:
- CatLocker build hash:
- Executable hash:
- Tested machine:
- Tested input devices:
- Stream Deck present: yes / no / pending
- Physical F24 sender present: yes / no / pending
- `ISCC.exe` present: yes / no / pending

## Results

- Overall result:
- Notes:
- Pending items:

## Manual Scenarios

- [ ] Launch CatLocker and confirm it appears only in the tray.
- [ ] Confirm CatLocker starts unlocked after a normal launch.
- [ ] Confirm CatLocker starts unlocked after login startup.
- [ ] Confirm the tray tooltip reports the unlocked state.
- [ ] Confirm the tray menu includes `Lock Keyboard`, `Unlock Keyboard`, `Toggle Cat Mode`, `Settings`, `Start with Windows`, and `Exit`.
- [ ] Confirm the mouse remains usable while CatLocker is running.
- [ ] Confirm `F24` locks CatLocker from a Stream Deck or equivalent sender.
- [ ] Confirm repeated `F24` presses toggle lock state exactly once per physical press.
- [ ] Confirm held `F24` autorepeat does not produce extra toggles.
- [ ] Confirm a configured shortcut such as `Ctrl+Shift+K` toggles lock state.
- [ ] Confirm the configured shortcut does not leak an extra trigger into Notepad.
- [ ] Confirm no stuck modifiers remain after locking or unlocking with a configured shortcut.
- [ ] Confirm exact `Left Ctrl + Right Ctrl` forces CatLocker unlocked while locked.
- [ ] Confirm exact `Left Ctrl + Right Ctrl` does not lock CatLocker when already unlocked.
- [ ] Confirm tray `Unlock Keyboard` works with the mouse while locked.
- [ ] Confirm tray `Unlock Keyboard` is disabled or unavailable while already unlocked.
- [ ] Confirm `Settings` is disabled while locked.
- [ ] Confirm `Settings` opens and saves only while unlocked.
- [ ] Confirm shortcut changes are validated before being saved.
- [ ] Confirm known Windows system shortcuts show the expected warning behavior.
- [ ] Confirm `Ctrl+Alt+Delete` is documented as unsupported and is not tested as an intercept case.
- [ ] Confirm function keys, including `F24`, are handled as expected by the selected sender.
- [ ] Confirm Windows keys, media keys, and volume keys behave as documented by the hook.
- [ ] Confirm many simultaneous held keys do not break recovery.
- [ ] Confirm held modifiers across lock and unlock transitions do not create stuck keys.
- [ ] Confirm keys released in an unusual order still leave applications in a clean state.
- [ ] Confirm the tray remains usable after an Explorer restart.
- [ ] Confirm notification preference changes persist correctly.
- [ ] Confirm portable mode uses `catlocker.toml` beside the executable.
- [ ] Confirm installed mode uses `%LOCALAPPDATA%\CatLocker\config.toml`.
- [ ] Confirm no telemetry, network access, or update behavior is present.
- [ ] Confirm `WH_KEYBOARD_LL` is the keyboard mechanism in use and no mouse hook is installed.
- [ ] Confirm elevated windows do not claim any special bypass or evasion behavior.
- [ ] Confirm proprietary HID / anti-cheat limitations are documented without evasion claims.
- [ ] Confirm exit while locked restores normal keyboard behavior and removes the tray icon.
- [ ] Confirm `LICENSE` and upstream Axorax/Keylock credit are present in the release docs.

## Evidence

- Screenshot or photo references:
- Log or note references:
- Open issues:

## Pending Hardware / Tools

- Physical Stream Deck available: pending
- `ISCC.exe` available: pending
- If either item is unavailable, record that the scenario could not be completed physically and why.
