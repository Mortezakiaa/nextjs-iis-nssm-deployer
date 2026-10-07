"""Build dist\\NextDeployer.exe: one file, no install needed, asks for admin rights (UAC) on launch.
Usage:  python build.py"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if subprocess.call([sys.executable, str(ROOT / "tests" / "test_core.py")]) != 0:
    sys.exit("self-check failed, not building")

sys.exit(subprocess.call([
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--console", "--uac-admin",
    "--name", "NextDeployer",
    "--paths", str(ROOT / "src"),
    "--collect-submodules", "rich._unicode_data",  # rich loads its width tables dynamically
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
    str(ROOT / "src" / "main.py"),
]))
