"""Constants and defaults. Any key in DEFAULTS can be overridden in C:\\ProgramData\\NextDeployer\\settings.json."""
import json
import os
from pathlib import Path

WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))
APPCMD = WINDIR / "System32" / "inetsrv" / "appcmd.exe"

DATA_DIR = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "NextDeployer"
REGISTRY_FILE = DATA_DIR / "apps.json"
LOG_FILE = DATA_DIR / "deployer.log"
SETTINGS_FILE = DATA_DIR / "settings.json"

DEFAULTS = {
    "port_min": 3001,           # Node ports
    "port_max": 3999,
    "logs_root": str(DATA_DIR / "logs"),
    "max_upload_mb": 100,
    "websockets": False,
    "rollback": True,
    "health_timeout": 60,
    "health_interval": 2,
}


def _load() -> dict:
    s = dict(DEFAULTS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text("utf-8")))
    except (OSError, ValueError):
        pass
    return s


S = _load()
PORT_RANGE = (int(S["port_min"]), int(S["port_max"]))
LOGS_ROOT = Path(S["logs_root"])
MAX_UPLOAD_BYTES = int(S["max_upload_mb"]) * 1024 * 1024
WEBSOCKETS = bool(S["websockets"])
ROLLBACK = bool(S["rollback"])
HEALTH_TIMEOUT = int(S["health_timeout"])
HEALTH_INTERVAL = int(S["health_interval"])

SERVICE_DESCRIPTION = "Next.js app - deployed by NextDeployer"
PROXY_SETTINGS = {  # system.webServer/proxy attributes required for Next.js Server Actions behind ARR
    "enabled": "True",
    "preserveHostHeader": "True",
    "reverseRewriteHostInResponseHeaders": "True",
    "timeout": "00:02:00",
}
