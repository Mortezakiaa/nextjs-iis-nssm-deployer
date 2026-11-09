"""Build into dist\\:
  NextDeployer.exe  one file, no install needed, asks for admin rights (UAC) on launch.
  NextDeployer.zip  the official embeddable Python + src + its libraries, for the pipeline; run
                    python\\python.exe app\\main.py. Antivirus quarantines the unsigned PyInstaller exe on servers.
Usage:  python build.py"""
import platform
import re
import shutil
import subprocess
import sys
import urllib.request
from importlib.metadata import distribution
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def runtime_distributions() -> list:
    names = [r.split("==")[0] for r in (ROOT / "requirements.txt").read_text().split() if not r.startswith("pyinstaller")]
    found = {}
    while names:
        d = distribution(names.pop())
        if d.name.lower() not in found:
            found[d.name.lower()] = d
            names += [re.split(r"[\s;<>=~!(\[]", r)[0] for r in d.requires or [] if "extra ==" not in r]
    return list(found.values())


def build_zip() -> None:
    version = platform.python_version()
    embed = ROOT / "build" / f"python-{version}-embed-amd64.zip"
    if not embed.exists():
        embed.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(f"https://www.python.org/ftp/python/{version}/{embed.name}", embed)
    out = ROOT / "dist" / "NextDeployer"
    shutil.rmtree(out, ignore_errors=True)
    shutil.unpack_archive(embed, out / "python")
    # The embeddable Python ignores PYTHONPATH; only its ._pth file adds folders to sys.path.
    pth = out / "python" / f"python{sys.version_info.major}{sys.version_info.minor}._pth"
    pth.write_text(pth.read_text().rstrip("\n") + "\n..\\app\n..\\lib\n")
    shutil.copytree(ROOT / "src", out / "app", ignore=shutil.ignore_patterns("__pycache__"))
    for d in runtime_distributions():
        for f in d.files:
            if f.parts[0] != ".." and "__pycache__" not in f.parts:
                (out / "lib" / f).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f.locate(), out / "lib" / f)
    if subprocess.call([str(out / "python" / "python.exe"), "-c", "import main"]) != 0:
        sys.exit("the zip's Python cannot load NextDeployer, not building")
    shutil.make_archive(str(out), "zip", out)


if subprocess.call([sys.executable, str(ROOT / "tests" / "test_core.py")]) != 0:
    sys.exit("self-check failed, not building")

build_zip()

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
