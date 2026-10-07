"""Central registry (C:\\ProgramData\\NextDeployer\\apps.json): source of truth for Update, Uninstall and the app
list. Rebuilt from the server itself when missing or unreadable. Writes are atomic (temp file + replace)."""
import json
import os
import tempfile
import time
from datetime import datetime

import config
import iis
import ports
from logger import log


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read():
    """dict, or None when the file is missing or broken (a broken file is kept aside for inspection)."""
    try:
        data = json.loads(config.REGISTRY_FILE.read_text("utf-8"))
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        log.exception("registry unreadable")
    try:
        os.replace(config.REGISTRY_FILE, f"{config.REGISTRY_FILE}.broken-{int(time.time())}")
    except OSError:
        pass
    return None


def load() -> dict:
    apps = _read()
    return rescan() if apps is None else apps


def save(apps: dict) -> None:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config.DATA_DIR, prefix="apps.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(apps, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, config.REGISTRY_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find(apps: dict, name: str):
    """Registry key matching name case-insensitively (Windows service/site names are case-insensitive)."""
    return next((k for k in apps if k.lower() == name.lower()), None)


def upsert(name: str, **fields) -> None:
    apps = _read() or {}
    apps.setdefault(name, {}).update(fields)
    save(apps)


def remove(name: str) -> None:
    apps = _read() or {}
    if apps.pop(name, None) is not None:
        save(apps)


def new_entry(name: str, port: int, deploy_path: str, node_path: str, log_dir: str) -> dict:
    return {
        "port": port,
        "deploy_path": deploy_path,
        "nssm_service": name,
        "iis_site": name,
        "node_path": node_path,
        "log_dir": log_dir,
        "backup_path": deploy_path.rstrip("\\/") + "_backup",
        "created_at": now(),
        "last_deploy_at": None,
        "last_deploy_status": "installing",
    }


def norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p)) if p else ""


def rescan() -> dict:
    """Rebuild the registry from NSSM services running node + IIS sites (matched by folder, port, then name)."""
    old = _read() or {}
    site_paths = iis.site_root_paths()
    site_by_path = {norm(p): s for s, p in site_paths.items()}
    site_by_port = {}
    for s, p in site_paths.items():
        for port in ports.webconfig_ports(p):
            site_by_port.setdefault(port, s)

    found = {}
    for svc, params in ports.nssm_services().items():
        app = str(params.get("Application", ""))
        if os.path.basename(app).lower() != "node.exe" and "server.js" not in str(params.get("AppParameters", "")).lower():
            continue
        app_dir = os.path.expandvars(str(params.get("AppDirectory", "")))
        port = ports.env_port(params)
        site = site_by_path.get(norm(app_dir)) or site_by_port.get(port) or (svc if svc in site_paths else None)
        prev = old.get(find(old, svc) or "", {})
        stdout = str(params.get("AppStdout") or "")
        found[svc] = {
            "port": port,
            "deploy_path": app_dir,
            "nssm_service": svc,
            "iis_site": site,
            "node_path": app,
            "log_dir": os.path.dirname(stdout) if stdout else prev.get("log_dir") or str(config.LOGS_ROOT / svc),
            "backup_path": prev.get("backup_path") or app_dir.rstrip("\\/") + "_backup",
            "created_at": prev.get("created_at"),
            "last_deploy_at": prev.get("last_deploy_at"),
            "last_deploy_status": prev.get("last_deploy_status") or "discovered",
        }
    save(found)
    log.info("registry rebuilt: %s", ", ".join(found) or "(no apps)")
    return found
