import subprocess
import sys


subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=CatLocker",
        "--onefile",
        "--noconsole",
        "--icon=assets/icon.ico",
        "--add-data=assets/icon.ico;assets",
        "main.py",
    ],
    check=True,
)
