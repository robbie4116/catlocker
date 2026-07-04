import os
import threading
import tomllib
import tomli_w

CONFIG_PATH = "keylock.toml"

DEFAULT_CONFIG = {
    "general": {
        "unlock": "ctrl+q",
        "refresh_rate": 1500,
        "quit_after": "never",
    },
    "startup": {
        "lock_keyboard": False,
        "lock_mouse": False,
    },
}

COMMENTS = """\
# [general]
#   unlock       - Shortcut to unlock (examples: ctrl+q, alt+s, shift+ctrl+q)
#   refresh_rate - Check for lock state every x milliseconds (integer only)
#   quit_after   - Exit app after some time ("never" or milliseconds as integer, e.g. 5000)
#
# [startup]
#   lock_keyboard - Lock keyboard on launch (true or false)
#   lock_mouse    - Lock mouse on launch (true or false)
#
# NOTE: The "Mouse lock" button is a bit buggy. When you lock only the mouse,
#       if the exit shortcut contains "ctrl", only a-z characters will work.
#       This is not an issue when locking only the keyboard or both.

"""


def _write_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write(COMMENTS)
        f.write(tomli_w.dumps(config))


def open_config() -> dict:
    """Load config from disk, creating it with defaults if missing or unreadable."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "rb") as f:
                loaded = tomllib.load(f)

            # Merge with defaults so missing keys are always present
            config = {
                "general": {**DEFAULT_CONFIG["general"], **loaded.get("general", {})},
                "startup": {**DEFAULT_CONFIG["startup"], **loaded.get("startup", {})},
            }
            return config
        except Exception as e:
            print(f"Failed to read config, recreating with defaults: {e}")

    _write_config(DEFAULT_CONFIG)
    return DEFAULT_CONFIG.copy()


def save_config(
    unlock: str = None, *, section: str = "general", key: str = None, value=None
):
    """
    Persist a single value to the config file.

    Convenience shortcuts:
        save_config("ctrl+q")                         # update unlock shortcut
        save_config(key="refresh_rate", value=2000)   # update any other key
    """

    def main():
        config = open_config()

        if unlock is not None:
            config["general"]["unlock"] = unlock
        elif section and key is not None:
            config.setdefault(section, {})[key] = value

        _write_config(config)

    thread = threading.Thread(target=main, daemon=True)
    thread.start()
    thread.join()
