# CatLocker

CatLocker is a Windows 11-only tray utility that locks keyboard input while leaving the mouse usable. It is designed to sit in the notification area, always starts unlocked, and recover from the mouse or an exact `Left Ctrl + Right Ctrl` chord.

The default setup for new installs is a single toggle shortcut, `F24`, which makes CatLocker practical with a Stream Deck or any other device that can send `F24`.

Settings offers a **Use separate lock and unlock shortcuts** checkbox above the shortcut recorder:

- Unchecked (single mode, the default): one **Lock / unlock shortcut** row is shown. The same shortcut both locks and unlocks — pressing it toggles Cat Mode.
- Checked (separate mode): two rows are shown, **Lock shortcut** and **Unlock shortcut**, each with its own record button. Each shortcut only performs its own action; pressing Lock while already locked, or Unlock while already unlocked, does nothing.

CatLocker remembers the toggle binding and both halves of the separate-mode pair independently, even for the mode that isn't currently active. Switching the checkbox, saving, and restarting the app all preserve whichever bindings you last recorded in each mode — recording a new toggle shortcut does not erase a previously recorded separate-mode pair, and vice versa. Clicking Cancel or closing the Settings window discards any unsaved checkbox or recording changes.

Upgrading from a version that only had a single Lock keyboard shortcut and a single Unlock keyboard shortcut preserves that configuration unchanged: if the two were already different (for example, a Stream Deck bound to `F23` for lock and `F24` for unlock), CatLocker migrates to separate mode automatically and keeps both bindings as they were. If the two were the same, it migrates to single mode with that shared shortcut as the toggle. No existing shortcut is renamed, cleared, or silently changed by the upgrade.

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
- Press your configured unlock keyboard shortcut.

The tray menu also includes `Lock Keyboard` and `Toggle Cat Mode`. When the app is locked, the tray `Unlock Keyboard` command remains available and the Settings window is disabled until the keyboard is unlocked again.

## Shortcuts

CatLocker accepts shortcuts in modifier-plus-trigger form. Examples:

- `F24`
- `LCtrl + K`
- `RCtrl + LShift + K`
- `OEM_3` (the backtick key identity)
- `VolumeUp` when a supported Fn combination produces that native key event

New recordings preserve the physical modifier side. A standalone shortcut is exactly one modifier, such as
`RAlt`; it performs its action on release only when no other key was held or pressed during that hold. Modifier-plus-trigger
shortcuts perform their action on the trigger key-down. While locked, standalone modifier events are still blocked and
the release performs the unlock transition without leaving an unmatched key event.

Existing generic shortcuts such as `Ctrl+Shift+K` remain generic and continue to accept either or both physical
sides until they are rerecorded. They are not rewritten during startup or an unrelated settings save. The Settings
display uses spaces around separators, while persisted values use stable canonical tokens such as `LCtrl+K`.

Punctuation is recorded by its Windows virtual-key identity (`OEM_1`, `OEM_PLUS`, `OEM_COMMA`, `OEM_MINUS`,
`OEM_PERIOD`, `OEM_2`, `OEM_3`, `OEM_4`, `OEM_5`, `OEM_6`, `OEM_7`, and `OEM_102`) rather than by the character
produced by text entry. Labels are resolved for the active layout when possible; `OEM_3` is shown as `Backtick` on
an identified US layout and otherwise uses a generic OEM label. Changing layouts does not rewrite saved identities.

Validation is strict:

- The shortcut must contain exactly one trigger key.
- A standalone recording must contain exactly one side-specific modifier; multi-modifier-only chords are rejected.
- Duplicate keys are rejected.
- The exact recovery chord is not configurable as a normal shortcut.
- Some Windows system shortcuts, such as `Alt+Tab`, `Win+L`, `Win+R`, and `Win+Shift+S`, may show a warning and require explicit confirmation.

Some Fn combinations do not produce a hook event; CatLocker keeps recording and shows a hint rather than inventing a
key. Standalone Alt and Win taps may retain their native menu or Start behavior. AltGr may produce additional Ctrl
events, so it is not treated as an isolated `RAlt` tap unless the native hook reports that unambiguously. These
interactions require physical Windows and keyboard-layout verification.

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

CatLocker is a fork of Keylock by Axorax. The upstream credit is preserved, and the project remains under the AGPL-3.0 license. See [LICENSE](LICENSE).
