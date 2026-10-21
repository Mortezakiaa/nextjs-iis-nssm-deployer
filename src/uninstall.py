"""Remove an app completely. Every step tolerates things already removed by hand."""
import os

import archive
import iis
import nssm
import prerequisites
import registry
import ui
from logger import log


def _try(label: str, fn) -> None:
    """fn returns False when there was nothing to remove, raises when removal failed."""
    try:
        if fn() is False:
            ui.info(label + ": از قبل وجود نداشت")
        else:
            ui.ok(label + ": انجام شد")
    except Exception:
        log.exception("uninstall step failed: %s", label)
        ui.warn(label + ": انجام نشد (جزئیات در فایل گزارش ثبت شد)")


def run(name: str, app: dict) -> None:
    tools = prerequisites.check_server(full=False)
    svc, site = app.get("nssm_service") or name, app.get("iis_site") or ""
    deploy, logs = app.get("deploy_path") or "", app.get("log_dir") or ""
    releases = app.get("releases_path") or (deploy.rstrip("\\/") + "_releases" if deploy else "")
    # Versions before the releases folder kept a single copy in <app folder>_backup.
    backup = app.get("backup_path") or (deploy.rstrip("\\/") + "_backup" if deploy else "")

    ui.title(f"حذف برنامه {name}")
    ui.info("این موارد حذف می‌شوند:")
    ui.info(f"- سرویس ویندوز: {svc}")
    if site:
        ui.info(f"- سایت IIS و Application Pool آن: {site}")
    ui.info(f"- آزاد شدن پورت {app.get('port')}")
    del_files = bool(deploy) and ui.confirm(f"پوشه برنامه ({deploy}) و نسخه‌های نگه‌داشته آن ({releases}) هم پاک شود؟")
    del_logs = bool(logs) and ui.confirm(f"پوشه گزارش‌های برنامه ({logs}) هم پاک شود؟")
    if not ui.confirm(f"آیا مطمئن هستید که برنامه «{name}» حذف شود؟ این کار برگشت‌پذیر نیست."):
        ui.info("حذف لغو شد. هیچ تغییری داده نشد.")
        return
    log.info("uninstall %s files=%s logs=%s", name, del_files, del_logs)

    def remove_service():
        if not nssm.exists(svc):
            return False
        nssm.stop(tools.nssm, svc, app.get("port") or 0)
        nssm.remove(tools.nssm, svc)

    _try("توقف و حذف سرویس ویندوز", remove_service)
    if site:
        _try("حذف سایت از IIS", lambda: iis.delete_site(site))
        _try("حذف Application Pool", lambda: iis.delete_apppool(site))
    if del_files:
        _try("حذف پوشه برنامه", lambda: os.path.isdir(deploy) and archive.rmtree(deploy))
        _try("حذف نسخه‌های نگه‌داشته", lambda: os.path.isdir(releases) and archive.rmtree(releases))
        if os.path.isdir(backup):
            _try("حذف نسخه پشتیبان قدیمی", lambda: archive.rmtree(backup))
    if del_logs:
        _try("حذف گزارش‌ها", lambda: os.path.isdir(logs) and archive.rmtree(logs))
    _try("حذف از فهرست برنامه‌ها", lambda: registry.remove(name))
    ui.ok(f"برنامه «{name}» حذف شد و پورت {app.get('port')} آزاد شد.")
