from pathlib import Path
import re
import sys

import main


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_has_no_third_party_dependencies():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pynput" not in text.casefold()
    assert "six" not in text.casefold()


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
