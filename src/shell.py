"""Run external tools (appcmd, nssm, netstat, netsh) with defensive output decoding and full logging."""
import codecs
import ctypes
import subprocess
from dataclasses import dataclass

from logger import log


class DeployError(Exception):
    """A failure with a Persian, operator-friendly message."""


@dataclass
class Result:
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def text(self) -> str:
        return (self.out + "\n" + self.err).strip()


def decode(data: bytes) -> str:
    """Console tools emit UTF-16 (nssm), UTF-8, or the OEM/ANSI code page (cp437/cp1256...). Never raise."""
    if not data:
        return ""
    if data.startswith(codecs.BOM_UTF16_LE) or data[1::2].count(0) > len(data) // 4:
        return data.decode("utf-16-le", "replace").lstrip("﻿").replace("\x00", "")
    encodings = ["utf-8"]
    try:
        encodings += [f"cp{ctypes.windll.kernel32.GetOEMCP()}", f"cp{ctypes.windll.kernel32.GetACP()}"]
    except AttributeError:  # not Windows
        pass
    for enc in encodings:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            pass
    return data.decode("utf-8", "replace")


def run(cmd: list, timeout: int = 120) -> Result:
    cmd = [str(c) for c in cmd]
    log.info("RUN %s", subprocess.list2cmdline(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        r = Result(p.returncode, decode(p.stdout), decode(p.stderr))
    except (OSError, subprocess.TimeoutExpired) as e:
        r = Result(-1, "", f"{type(e).__name__}: {e}")
    log.info("  exit=%s %s", r.code, r.text[:3000])
    return r


def check(r: Result, message: str) -> Result:
    if not r.ok:
        raise DeployError(message)
    return r
