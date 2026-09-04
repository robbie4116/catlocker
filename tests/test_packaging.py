from pathlib import Path
import re
import sys

import main
from tray import build_menu_state


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_has_no_third_party_dependencies():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pyn" + "put" not in text.casefold()
    assert "s" + "ix" not in text.casefold()


def test_build_and_installer_use_catlocker_identity():
    combined = "\n".join(
        (ROOT / name).read_text(encoding="utf-8")
        for name in ("build.py", "build.bat", "catlocker.iss")
    )
    installer = (ROOT / "catlocker.iss").read_text(encoding="utf-8")
    assert "CatLocker" in combined
    assert "keylock.exe" not in combined.casefold()
    assert re.search(
        r"(?m)^AppId=\{\{[0-9A-F-]{36}\}$",
        installer,
    )
    assert "--add-data" in (ROOT / "build.py").read_text(encoding="utf-8")
    assert "assets/icon.ico" in (
        ROOT / "build.py"
    ).read_text(encoding="utf-8").replace("\\", "/")
    assert "check=True" in (ROOT / "build.py").read_text(encoding="utf-8")


def test_legacy_input_and_mouse_assets_are_removed():
    assert not (ROOT / "core.py").exists()
    assert not (ROOT / "assets" / "mouse_locked.png").exists()
    assert not (ROOT / "assets" / "mouse_unlocked.png").exists()


def test_source_resource_path_resolves_existing_icon(monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    icon = main.resource_path("assets/icon.ico")
    assert icon == ROOT / "assets" / "icon.ico"
    assert icon.exists()


def test_frozen_resource_path_uses_meipass(monkeypatch, tmp_path):
    frozen_assets = tmp_path / "assets"
    frozen_assets.mkdir()
    frozen_icon = frozen_assets / "icon.ico"
    frozen_icon.write_bytes((ROOT / "assets" / "icon.ico").read_bytes())
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert main.resource_path("assets/icon.ico") == frozen_icon
    assert main.resource_path("assets/icon.ico").exists()


def test_main_has_no_legacy_runtime_or_mouse_references():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    for forbidden in (
        "legacy_" + "main",
        "import " + "core",
        "lock_" + "mouse",
        "refresh_" + "rate",
        "load_" + "asset",
        "mouse_" + "locked.png",
        "mouse_" + "unlocked.png",
    ):
        assert forbidden not in source


def test_tray_uses_catlocker_keyboard_labels():
    menu = build_menu_state(locked=False, startup_enabled=False)
    assert [item.label for item in menu.items[:3]] == [
        "Lock Keyboard",
        "Unlock Keyboard",
        "Toggle Cat Mode",
    ]


def test_release_metadata_points_to_catlocker_fork_and_preserves_attribution():
    pad = (ROOT / "PAD.xml").read_text(encoding="utf-8")
    assert "<url>https://github.com/robbie4116/catlocker</url>" in pad
    assert "https://github.com/robbie4116/catlocker/releases/" in pad
    assert "https://raw.githubusercontent.com/robbie4116/catlocker/main/thumbnail.png" in pad
    assert "https://raw.githubusercontent.com/robbie4116/catlocker/main/assets/icon.png" in pad
    assert "Axorax" in pad
    assert "https://axorax.github.io/" in pad
    assert "axorax" in pad.casefold()
    assert "configurable toggle shortcut" in pad.casefold()
    assert "configurable emergency unlock shortcut" not in pad.casefold()


def test_release_metadata_uses_agpl_and_windows_11_minimum():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    installer = (ROOT / "catlocker.iss").read_text(encoding="utf-8")
    assert "AGPL-3.0" in readme
    assert installer.count("MinVersion=10.0.22000") == 1
