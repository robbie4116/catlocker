<p align="center">
    <img src="./assets/icon.png" width="100" height="100"/>
</p>

<h3 align="center">keylock</h3>

<p align="center">Lock your keys with ease</p>

## 💠 Preview

<p align="center">
    <img src="./thumbnail.png" />
</p>

## 🎛️ Config File

You can make a file called `keylock.toml` in the directory where the executable is. It will be used by the app to load the settings. All config options are:

```
[general]
unlock = "ctrl+q"     - Shortcut to unlock (examples: ctrl+q, alt+s, shift+ctrl+q)
refresh_rate = 1500   - Check for lock state every x milliseconds (integer only)
quit_after = "never"  - Exit app after some time ("never" or milliseconds as integer, e.g. 5000)

[startup]
lock_keyboard = false - Lock keyboard on launch (true or false)
lock_mouse = false    - Lock mouse on launch (true or false)
```

> [!Important]
> The "Mouse lock" button is a bit buggy. When you lock only mouse, if exit shortcut has "ctrl" then you can only use a-z characters and nothing else. This is not an issue when you lock only keyboard or both.

---

<p align="center"><a href="https://www.patreon.com/axorax">Support me on Patreon</a> — <a href="https://github.com/axorax/socials">Check out my socials</a></p>
