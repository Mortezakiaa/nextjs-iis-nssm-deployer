"""Automatic rollback after a failed update. Isolated on purpose: set "rollback": false in settings.json to
turn off the automatic rollback after a failed health check, or replace this module without touching the rest."""
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


def restore(app: dict, nssm_path: str):
    """Put the backup back and restart. Returns the new Health, or None if there was nothing to restore."""
    backup = app.get("backup_path") or ""
    if not os.path.isdir(backup):
        ui.error("نسخه پشتیبان پیدا نشد؛ برگشت به نسخه قبلی ممکن نیست.")
        return None
    ui.warn("در حال برگشت به نسخه قبلی برنامه...")
    log.info("rollback %s from %s", app["nssm_service"], backup)
    with ui.busy("در حال بازگرداندن نسخه قبلی..."):
        nssm.stop(nssm_path, app["nssm_service"], app["port"])
        archive.restore(backup, app["deploy_path"])
        webconfig.write(app["deploy_path"], app["port"])
        nssm.start(nssm_path, app["nssm_service"])
    return healthcheck.check(app, nssm_path)
