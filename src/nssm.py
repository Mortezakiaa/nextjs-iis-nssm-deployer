"""nssm.exe wrappers: install, configure, start, stop, remove, status."""
import os
import time
import winreg

import config
import ports
from shell import check, run

FAIL = "تنظیم سرویس ویندوز (NSSM) انجام نشد."


def exists(name: str) -> bool:
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SYSTEM\CurrentControlSet\Services\{name}"))
        return True
    except OSError:
        return False


def status(nssm: str, name: str) -> str:
    """SERVICE_RUNNING / SERVICE_STOPPED / SERVICE_PAUSED ... or '' when the service doesn't exist."""
    r = run([nssm, "status", name], 30)
    return r.out.strip() if r.ok else ""


def env_args(port: int, current=()) -> list:
    """Our three variables, plus any others already set on the service (e.g. DATABASE_URL added by the team)."""
    ours = [f"PORT={port}", "NODE_ENV=production", "HOSTNAME=127.0.0.1"]
    keep = [e for e in ([current] if isinstance(current, str) else current or [])
            if e.partition("=")[0].strip().upper() not in ("PORT", "NODE_ENV", "HOSTNAME")]
    return ours + keep


def install(nssm: str, name: str, node: str, app_dir: str, port: int, log_dir: str) -> None:
    check(run([nssm, "install", name, node, "server.js"]), "ساخت سرویس ویندوز (NSSM) انجام نشد.")
    configure(nssm, name, app_dir, port, log_dir)


def configure(nssm: str, name: str, app_dir: str, port: int, log_dir: str) -> None:
    """Idempotent: also used on update so older services get logging, recovery and localhost binding."""
    os.makedirs(log_dir, exist_ok=True)
    current = ports.nssm_services().get(name, {}).get("AppEnvironmentExtra")
    for args in (
        ["AppDirectory", app_dir],
        ["AppEnvironmentExtra", *env_args(port, current)],
        ["AppStdout", os.path.join(log_dir, "out.log")],
        ["AppStderr", os.path.join(log_dir, "err.log")],
        ["AppRotateFiles", "1"],
        ["AppRotateOnline", "1"],
        ["AppRotateBytes", "10485760"],
        ["AppExit", "Default", "Restart"],
        ["AppRestartDelay", "5000"],
        ["AppStopMethodSkip", "0"],
        ["Start", "SERVICE_AUTO_START"],
        ["Description", config.SERVICE_DESCRIPTION],
    ):
        check(run([nssm, "set", name, *args]), FAIL)


def start(nssm: str, name: str) -> bool:
    # A crashing app can make `nssm start` report an error; the health check gives the real diagnosis.
    return run([nssm, "start", name], 90).ok


def stop(nssm: str, name: str, port: int = 0, timeout: int = 60) -> bool:
    """Stop and wait until the service is stopped and Node released its port (so its file locks are gone)."""
    run([nssm, "stop", name], 90)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if status(nssm, name) in ("SERVICE_STOPPED", "") and not (port and ports.listening("127.0.0.1", port)):
            return True
        time.sleep(1)
    return False


def remove(nssm: str, name: str) -> None:
    check(run([nssm, "remove", name, "confirm"]), "حذف سرویس ویندوز انجام نشد.")
