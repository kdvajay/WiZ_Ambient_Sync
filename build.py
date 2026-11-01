import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
# Version-specific output directories prevent PyInstaller from trying to
# delete/overwrite an executable that Windows or OneDrive still has locked.
DIST = ROOT / "dist" / VERSION
WORK = ROOT / "build" / VERSION


def run(args):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True)


def main():
    if sys.platform not in ("win32", "linux"):
        raise SystemExit("Build this release on Windows or Linux.")
    sep = ";" if sys.platform == "win32" else ":"
    DIST.mkdir(parents=True, exist_ok=True)
    (WORK / "engine").mkdir(parents=True, exist_ok=True)
    (WORK / "gui").mkdir(parents=True, exist_ok=True)

    common = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
              "--onefile", "--distpath", DIST]

    engine_args = common + [
        "--workpath", WORK / "engine", "--specpath", WORK / "spec-engine",
        "--name", "wiz-engine", "--console",
        "--icon", ROOT / "app" / "app_icon.ico",
        "--paths", ROOT / "engine",
        "--collect-all", "soundcard",
        "--collect-all", "pywizlight",
        "--collect-all", "numpy",
        ROOT / "engine" / "WiZ_Ambient_Sync.py",
    ]
    run(engine_args)

    gui_args = common + [
        "--workpath", WORK / "gui", "--specpath", WORK / "spec-gui",
        "--name", "WiZAmbientSync", "--windowed",
        "--icon", ROOT / "app" / "app_icon.ico",
        "--add-data", f"{ROOT / 'app' / 'config.json'}{sep}.",
        "--add-data", f"{ROOT / 'app' / 'app_icon.png'}{sep}.",
        "--add-data", f"{ROOT / 'app' / 'chevron-light.png'}{sep}.",
        "--add-data", f"{ROOT / 'app' / 'chevron-dark.png'}{sep}.",
        "--collect-all", "PySide6",
        "--collect-all", "platformdirs",
        "--collect-all", "pywizlight",
        "--hidden-import", "mss",
        ROOT / "app" / "main.py",
    ]
    run(gui_args)

    print("\nBuild complete. Distribute BOTH files from:", DIST)
    print("  WiZAmbientSync" + (".exe" if sys.platform == "win32" else ""))
    print("  wiz-engine" + (".exe" if sys.platform == "win32" else ""))
    print("Keep them in the same folder.")

if __name__ == "__main__":
    main()
