"""Zip validation, extraction (flattening a single top-level folder), backup and folder swaps.
Every filesystem path goes through the \\\\?\\ prefix: standalone node_modules paths often exceed 260 chars."""
import os
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass

from logger import log
from shell import DeployError


@dataclass
class ZipSummary:
    path: str
    prefix: str        # '' or 'topfolder/' when everything sits in one folder
    size: int          # zip file size
    unpacked: int      # total uncompressed size
    has_static: bool   # .next/static present (CSS/JS assets)


def lp(path: str) -> str:
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def inspect(path: str) -> ZipSummary:
    if not os.path.isfile(path):
        raise DeployError("فایلی با این مسیر پیدا نشد. مسیر را دوباره بررسی کنید.")
    if not zipfile.is_zipfile(path):
        raise DeployError("این فایل یک فایل zip معتبر نیست.")
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            infos = z.infolist()
    except Exception:
        log.exception("zip unreadable: %s", path)
        bad = True
    if bad:
        raise DeployError("فایل zip خراب است یا قابل خواندن نیست. لطفاً فایل را دوباره از تیم فنی بگیرید.")

    names = {i.filename for i in infos}
    tops = {n.split("/")[0] for n in names if n.strip("/") and not n.startswith("__MACOSX")}
    if "server.js" in names:
        prefix = ""
    elif len(tops) == 1 and f"{next(iter(tops))}/server.js" in names:
        prefix = next(iter(tops)) + "/"
    else:
        raise DeployError("فایل server.js داخل این zip پیدا نشد؛ این فایل خروجی درست برنامه نیست.\n"
                          "از تیم فنی بخواهید محتوای پوشه .next/standalone (همراه .next/static و public) را zip کنند.")
    return ZipSummary(path, prefix, os.path.getsize(path), sum(i.file_size for i in infos),
                      any(n.startswith(prefix + ".next/static/") for n in names))


def staging_dir(target: str) -> str:
    return target.rstrip("\\/") + ".nd-staging"


def stage(z: ZipSummary, target: str) -> str:
    """Extract next to target (same drive, so moving in is instant). Returns the folder that holds server.js."""
    staging = staging_dir(target)
    rmtree(staging)
    with zipfile.ZipFile(z.path) as zf:
        zf.extractall(lp(staging))
    return os.path.join(staging, z.prefix.rstrip("/")) if z.prefix else staging


def move_contents(src: str, dst: str) -> None:
    """Move src's entries into dst, replacing same-named ones; other files in dst are left alone."""
    os.makedirs(lp(dst), exist_ok=True)
    for name in os.listdir(lp(src)):
        remove(os.path.join(dst, name))
        os.replace(lp(os.path.join(src, name)), lp(os.path.join(dst, name)))


SELF_NAME = os.path.basename(sys.executable).lower()


def ours(name: str) -> bool:
    """Operators keep the zip and NextDeployer.exe inside the app folder. Never delete, back up or restore over
    them: the running exe is locked and would make every update fail."""
    n = name.lower()
    return n.endswith(".zip") or n == SELF_NAME


def _skip_ours(root: str):
    """copytree ignore= that leaves the operator's files at the top level of root out of the copy."""
    top = lp(root)
    return lambda d, names: [n for n in names if d == top and ours(n)]


def retry(fn, seconds: int = 30):
    """Windows keeps files locked for a moment after a process exits; retry instead of failing."""
    deadline = time.time() + seconds
    while True:
        try:
            return fn()
        except OSError:
            if time.time() > deadline:
                raise
            time.sleep(2)


def _make_writable(func, path, _exc):
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree(path: str) -> None:
    if path and os.path.lexists(lp(path)):
        retry(lambda: shutil.rmtree(lp(path), onexc=_make_writable))


def remove(path: str) -> None:
    """Delete a file or folder; nothing to do if it doesn't exist."""
    p = lp(path)
    if os.path.isdir(p) and not os.path.islink(p):
        retry(lambda: shutil.rmtree(p, onexc=_make_writable))
    elif os.path.lexists(p):
        retry(lambda: (os.chmod(p, stat.S_IWRITE), os.remove(p)))


def clear(path: str) -> None:
    """Delete everything inside path except the operator's files (see ours), keep the folder itself (IIS watches it)."""
    os.makedirs(lp(path), exist_ok=True)
    for name in os.listdir(lp(path)):
        if not ours(name):
            remove(os.path.join(path, name))


def backup(src: str, dst: str) -> None:
    """Copy src to dst, keeping only the newest backup. The old backup survives until the new copy is complete."""
    tmp = dst.rstrip("\\/") + ".nd-tmp"
    rmtree(tmp)
    shutil.copytree(lp(src), lp(tmp), symlinks=True, ignore=_skip_ours(src))
    rmtree(dst)
    os.replace(lp(tmp), lp(dst))


def restore(backup_dir: str, target: str) -> None:
    clear(target)
    shutil.copytree(lp(backup_dir), lp(target), symlinks=True, dirs_exist_ok=True, ignore=_skip_ours(backup_dir))


def existing_ancestor(path: str) -> str:
    path = os.path.abspath(path)
    while not os.path.isdir(path) and os.path.dirname(path) != path:
        path = os.path.dirname(path)
    return path


def writable(path: str) -> bool:
    try:
        with tempfile.TemporaryFile(dir=existing_ancestor(path)):
            return True
    except OSError:
        return False


def enough_space(path: str, z: ZipSummary, with_backup: bool) -> bool:
    need = max(z.size * 3, z.unpacked * (2 if with_backup else 1))
    try:
        return shutil.disk_usage(existing_ancestor(path)).free >= need
    except OSError:
        return False
