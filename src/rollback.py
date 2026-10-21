"""Putting a kept release back: automatically after a failed update, or on request (menu / `rollback`). Isolated on
purpose: set "rollback": false in settings.json to turn off the automatic rollback after a failed health check, or
replace this module without touching the rest."""
import os

import archive
import config
import healthcheck
import nssm
import ui
import webconfig
from logger import log


def enabled() -> bool:
    return config.ROLLBACK


def newest_release(app: dict, skip: str = "") -> str:
    """Folder of the newest kept release other than `skip`, or '' when there is none."""
    root = app.get("releases_path") or ""
    name = next((n for n in archive.releases(root) if n != skip), "")
    return os.path.join(root, name) if name else ""


def restore(app: dict, nssm_path: str, release: str = ""):
    """Put a kept release (the newest by default) back and restart. Returns the new Health, or None if there was
    nothing to restore."""
    release = release or newest_release(app)
    if not release or not os.path.isdir(release):
        ui.error("هیچ نسخه سالمی نگه داشته نشده است؛ برگشت به نسخه قبلی ممکن نیست.")
        return None
    ui.warn(f"در حال برگشت به نسخه {os.path.basename(release)}...")
    log.info("rollback %s from %s", app["nssm_service"], release)
    with ui.busy("در حال بازگرداندن نسخه قبلی..."):
        nssm.stop(nssm_path, app["nssm_service"], app["port"])
        archive.restore(release, app["deploy_path"])
        webconfig.write(app["deploy_path"], app["port"])
        nssm.start(nssm_path, app["nssm_service"])
    app["current_release"] = os.path.basename(release)
    return healthcheck.check(app, nssm_path)
