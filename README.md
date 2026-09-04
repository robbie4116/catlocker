# CatLocker

CatLocker is a Windows 11-only tray utility that locks keyboard input while leaving the mouse usable. It is designed to sit in the notification area, always starts unlocked, and recover from the mouse or an exact `Left Ctrl + Right Ctrl` chord.

The default setup is `F24`, which makes CatLocker practical with a Stream Deck or any other device that can send `F24`. You can also configure a different shortcut if it fits your workflow better.

## What it does

- Runs as a tray-first app with no permanent normal window.
- Uses one transparent `WH_KEYBOARD_LL` low-level keyboard hook.
- Does not install a mouse hook.
- Always starts unlocked, including on login startup.
- Stores local configuration only.
- Does not include telemetry, network access, or an update checker.

## Recovery paths

If CatLocker is locked, you can always recover in one of these ways:

- Press the tray icon and choose `Unlock Keyboard`.
- Press the exact `Left Ctrl + Right Ctrl` chord.
- Use the configured keyboard shortcut again if you are intentionally toggling state.

The tray menu also includes `Lock Keyboard` and `Toggle Cat Mode`. When the app is locked, the tray `Unlock Keyboard` command remains available and the Settings window is disabled until the keyboard is unlocked again.

## Shortcuts

CatLocker accepts shortcuts in modifier-plus-trigger form. Examples:

- `F24`
- `Ctrl+Shift+K`
- `Ctrl+Alt+F12`
- `Win+R`

Validation is strict:

- The shortcut must contain exactly one trigger key.
- Modifier-only combinations are rejected.
- Duplicate keys are rejected.
- The exact recovery chord is not configurable as a normal shortcut.
- Some Windows system shortcuts, such as `Alt+Tab`, `Win+L`, `Win+R`, and `Win+Shift+S`, may show a warning and require explicit confirmation.

`Ctrl+Alt+Delete` is not interceptable by this hook and remains outside CatLocker control. CatLocker also does not claim compatibility with every proprietary HID key or every anti-cheat environment.

## Configuration

CatLocker keeps per-user settings locally.

- Installed mode: `%LOCALAPPDATA%\CatLocker\config.toml`
- Portable mode: `catlocker.toml` beside the executable, used only when that file already exists

The stored lock state is never persisted. Every launch begins unlocked.

## Limitations

CatLocker is intentionally conservative:

- It does not hide processes or conceal hooks.
- It does not claim to bypass UIPI, elevated-window restrictions, secure attention paths, proprietary HID drivers, or anti-cheat protections.
- It does not use driver-level input interception.
- It does not promise to intercept keys that Windows does not expose through `WH_KEYBOARD_LL`.

Those are platform or policy limits, not features to work around.

## Development

CatLocker targets Python 3.11+ for development, test, and packaging.

Run tests:

```powershell
py -m pytest -v
```

Run the Windows build:

```powershell
py build.py
```

Build the installer:

```powershell
ISCC.exe catlocker.iss
```

If `ISCC.exe` is unavailable on the machine, installer verification is pending.

## Credits and license

CatLocker is a fork of Keylock by Axorax. The upstream credit is preserved, and the project remains under the GPL. See [LICENSE](LICENSE).
