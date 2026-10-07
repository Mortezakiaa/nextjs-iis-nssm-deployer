"""NextDeployer entry point: main menu and the Install / Update wizards."""
import os
import sys

import archive
import config
import healthcheck
import iis
import logger
import nssm
import ports
import prerequisites
import registry
import rollback
import ui
import uninstall
import webconfig
from logger import log
from shell import DeployError

STATUS_FA = {"success": "موفق", "failed": "ناموفق", "rolled_back": "برگشت به نسخه قبلی",
             "rollback_failed": "برگشت ناموفق", "installing": "نیمه‌کاره", "discovered": "پیدا شده در بازبینی"}


# ---------------------------------------------------------------- shared questions
def ask_until(question: str, problem, default: str = "", path: bool = False, allow_empty: bool = False) -> str:
    while True:
        answer = (ui.ask_path if path else ui.ask)(question, default)
        if not answer and allow_empty:
            return ""
        p = problem(answer) if answer else "چیزی وارد نشد. لطفاً دوباره وارد کنید."
        if not p:
            return answer
        ui.error(p)


def ask_zip() -> archive.ZipSummary:
    while True:
        path = ui.ask_path("مسیر فایل zip برنامه را وارد کنید:")
        if not path:
            continue
        try:
            with ui.busy("در حال بررسی فایل zip..."):
                z = archive.inspect(os.path.abspath(path))
        except DeployError as e:
            ui.error(str(e))
            continue
        if not z.has_static:
            ui.warn("پوشه .next/static داخل این فایل نیست؛ ممکن است ظاهر سایت (رنگ‌ها، فونت‌ها، تصاویر) درست نمایش داده نشود.")
            if not ui.confirm("با همین فایل ادامه بدهیم؟"):
                continue
        ui.ok("فایل zip سالم است.")
        return z


def pick_app(apps: dict, question: str):
    names = sorted(apps, key=str.lower)
    labels = [f"{n} (پورت {apps[n].get('port')})" for n in names]
    i = ui.choose(question, labels + ["بازگشت به منوی اصلی"])
    return None if i == len(names) else names[i]


def remove_service(nssm_path: str, name: str, port: int) -> None:
    if nssm.exists(name):
        nssm.stop(nssm_path, name, port)
        nssm.remove(nssm_path, name)


# ---------------------------------------------------------------- install
def install() -> None:
    ui.title("نصب برنامه جدید")
    tools = prerequisites.check_server()
    apps = registry.load()
    z = ask_zip()
    name = ask_until("نام برنامه را به انگلیسی وارد کنید (مثلاً LandingPage):",
                     lambda n: prerequisites.name_problem(n, apps))
    dest = os.path.normpath(ask_until("برنامه در کدام پوشه سرور نصب شود؟ (مثلاً D:\\Apps\\MyApp)",
                                      lambda d: prerequisites.dest_problem(os.path.normpath(d), z), path=True))
    host = ask_until("دامنه سایت چیست؟ (مثلاً shop.example.com)\nاگر هنوز دامنه ندارید، فقط Enter بزنید.",
                     lambda h: prerequisites.host_problem(prerequisites.clean_host(h)), allow_empty=True)
    host = prerequisites.clean_host(host) if host else ""

    with ui.busy("در حال بررسی پورت‌های سرور..."):
        taken = ports.taken_ports(apps)
    iis_port = int(ask_until("سایت در IIS روی کدام پورت باز شود؟ (برای سایت با دامنه معمولاً 80)",
                             lambda p: prerequisites.iis_port_problem(p, host, taken)))
    with ui.busy("در حال پیدا کردن یک پورت آزاد برای برنامه..."):
        port = ports.pick(*config.PORT_RANGE, taken | {iis_port})
    if not port:
        raise DeployError("هیچ پورت آزادی در بازه تعیین‌شده پیدا نشد. به تیم فنی اطلاع دهید.")

    ui.info("")
    ui.info("خلاصه نصب:")
    ui.info(f"- نام برنامه: {name}")
    ui.info(f"- پوشه نصب: {dest}")
    ui.info(f"- پورت داخلی برنامه (خودکار انتخاب شد): {port}")
    suffix = "" if iis_port == 80 else f":{iis_port}"
    ui.info(f"- آدرس سایت: http://{host}{suffix}/" if host else f"- آدرس سایت: همین سرور، پورت {iis_port}")
    if os.path.isdir(dest) and os.listdir(dest):
        ui.warn("پوشه نصب خالی نیست: فایل‌های هم‌نام با فایل‌های برنامه جایگزین می‌شوند و بقیه فایل‌ها دست نمی‌خورند.")
    if not ui.confirm("نصب انجام شود؟"):
        ui.info("نصب لغو شد. هیچ تغییری روی سرور داده نشد.")
        return

    log_dir = str(config.LOGS_ROOT / name)
    app = registry.new_entry(name, port, dest, tools.node, log_dir)
    log.info("INSTALL name=%s dest=%s port=%s host=%s iis_port=%s zip=%s node=%s nssm=%s",
             name, dest, port, host, iis_port, z.path, tools.node, tools.nssm)
    n, undo = 9, []
    try:
        ui.step(1, n, "بررسی پیش‌نیازها: انجام شد")
        ui.step(2, n, f"رزرو پورت {port}")
        registry.upsert(name, **app)
        undo.append(lambda: registry.remove(name))

        ui.step(3, n, "باز کردن فایل zip در پوشه نصب")
        created = not os.path.exists(dest)
        undo.append(lambda: archive.rmtree(archive.staging_dir(dest)))
        with ui.busy("در حال باز کردن فایل‌ها..."):
            src = archive.stage(z, dest)
            added = os.listdir(src) + ["web.config"]  # all we put in; anything else in dest was there before
            undo.append(lambda: archive.rmtree(dest) if created else
                        [archive.remove(os.path.join(dest, x)) for x in added])
            archive.move_contents(src, dest)
            archive.rmtree(archive.staging_dir(dest))

        ui.step(4, n, "ساخت فایل تنظیمات IIS (web.config)")
        webconfig.write(dest, port)

        ui.step(5, n, "ساخت سایت در IIS")
        undo.append(lambda: iis.delete_apppool(name))
        undo.append(lambda: iis.delete_site(name))
        iis.create_site(name, dest, iis_port, host)

        ui.step(6, n, "ساخت سرویس ویندوز برای اجرای برنامه")
        undo.append(lambda: remove_service(tools.nssm, name, port))
        nssm.install(tools.nssm, name, tools.node, dest, port, log_dir)

        ui.step(7, n, "روشن کردن برنامه")
        nssm.start(tools.nssm, name)

        ui.step(8, n, "بررسی سلامت برنامه")
        h = healthcheck.check(app, tools.nssm)
    except BaseException:
        log.exception("install failed, undoing %d step(s)", len(undo))
        ui.warn("نصب کامل نشد. در حال پاک کردن تغییرات نیمه‌کاره تا سرور تمیز بماند...")
        if os.path.exists(os.path.join(log_dir, "err.log")):
            healthcheck.show_app_log(app)
        for fn in reversed(undo):
            try:
                fn()
            except Exception:
                log.exception("undo step failed")
        ui.info("تغییرات نیمه‌کاره پاک شد.")
        raise

    ui.step(9, n, "ثبت در فهرست برنامه‌ها")
    registry.upsert(name, last_deploy_at=registry.now(), last_deploy_status="success" if h.ok else "failed")
    log.info("INSTALL %s result node=%s iis=%s", name, h.node_code, h.iis_code)
    healthcheck.report(h, name, app)
    if not h.ok:
        ui.info("برنامه نصب شد ولی سالم کار نمی‌کند. پیام‌های بالا را برای تیم فنی بفرستید؛ "
                "بعد از رفع مشکل، نسخه درست را با گزینه «به‌روزرسانی» نصب کنید.")


# ---------------------------------------------------------------- update
def app_problem(app: dict) -> str:
    rescan_hint = " از منوی اصلی گزینه «بازبینی سرور» را بزنید و دوباره امتحان کنید."
    if not app.get("port") or not app.get("deploy_path") or not app.get("nssm_service"):
        return "اطلاعات این برنامه ناقص است." + rescan_hint
    if not nssm.exists(app["nssm_service"]):
        return "سرویس ویندوز این برنامه روی سرور پیدا نشد." + rescan_hint
    if not app.get("iis_site") or app["iis_site"] not in iis.sites():
        return "سایت این برنامه در IIS پیدا نشد." + rescan_hint
    if not os.path.isdir(app["deploy_path"]):
        return f"پوشه برنامه ({app['deploy_path']}) پیدا نشد." + rescan_hint
    return ""


def update() -> None:
    ui.title("به‌روزرسانی برنامه")
    tools = prerequisites.check_server()
    apps = registry.load()
    if not apps:
        ui.warn("هیچ برنامه‌ای روی این سرور پیدا نشد. برای نصب، گزینه «نصب برنامه جدید» را بزنید.")
        return
    name = pick_app(apps, "کدام برنامه به‌روزرسانی شود؟")
    if not name:
        return
    app = apps[name]
    problem = app_problem(app)
    if problem:
        raise DeployError(problem)
    svc, deploy, port = app["nssm_service"], app["deploy_path"], app["port"]
    app.setdefault("backup_path", deploy.rstrip("\\/") + "_backup")
    app["log_dir"] = app.get("log_dir") or str(config.LOGS_ROOT / name)

    z = ask_zip()
    if not archive.enough_space(deploy, z, with_backup=True):
        raise DeployError("فضای خالی درایو برای نسخه جدید و نسخه پشتیبان کافی نیست. کمی فضا آزاد کنید و دوباره امتحان کنید.")
    ui.info("")
    ui.info(f"برنامه «{name}» (پوشه {deploy}، پورت {port}) با فایل زیر به‌روزرسانی می‌شود:")
    ui.info(z.path)
    ui.info("در طول به‌روزرسانی، سایت برای چند ثانیه در دسترس نیست.")
    if not ui.confirm("به‌روزرسانی انجام شود؟"):
        ui.info("به‌روزرسانی لغو شد. هیچ تغییری روی سرور داده نشد.")
        return

    log.info("UPDATE name=%s deploy=%s port=%s zip=%s", name, deploy, port, z.path)
    n, stopped, files_touched = 7, False, False
    try:
        ui.step(1, n, "باز کردن نسخه جدید (برنامه فعلی هنوز روشن است)")
        with ui.busy("در حال باز کردن فایل‌ها..."):
            src = archive.stage(z, deploy)

        ui.step(2, n, "تهیه نسخه پشتیبان از نسخه فعلی")
        with ui.busy("در حال کپی نسخه فعلی..."):
            archive.backup(deploy, app["backup_path"])

        ui.step(3, n, "خاموش کردن برنامه")
        stopped = True
        with ui.busy("در حال خاموش کردن و صبر برای بسته شدن کامل برنامه..."):
            if not nssm.stop(tools.nssm, svc, port):
                log.warning("service %s did not stop in time", svc)

        ui.step(4, n, "جایگزینی فایل‌ها")
        files_touched = True
        with ui.busy("در حال جایگزینی فایل‌ها..."):
            archive.clear(deploy)
            archive.move_contents(src, deploy)
            archive.rmtree(archive.staging_dir(deploy))

        ui.step(5, n, "به‌روزرسانی تنظیمات")
        webconfig.write(deploy, port)
        nssm.configure(tools.nssm, svc, deploy, port, app["log_dir"])

        ui.step(6, n, "روشن کردن برنامه")
        nssm.start(tools.nssm, svc)

        ui.step(7, n, "بررسی سلامت برنامه")
        h = healthcheck.check(app, tools.nssm)
    except BaseException:
        log.exception("update failed (stopped=%s files_touched=%s)", stopped, files_touched)
        ui.warn("به‌روزرسانی کامل نشد. در حال برگرداندن برنامه به حالت قبل...")
        try:
            archive.rmtree(archive.staging_dir(deploy))
            if files_touched:
                h2 = rollback.restore(app, tools.nssm)
                if h2:
                    healthcheck.report(h2, name, app)
            elif stopped:
                nssm.start(tools.nssm, svc)
                ui.info("برنامه با همان نسخه قبلی دوباره روشن شد.")
            else:
                ui.info("هیچ تغییری روی برنامه داده نشده بود.")
            registry.upsert(name, last_deploy_at=registry.now(), last_deploy_status="failed")
        except Exception:
            log.exception("recovery after failed update also failed")
            ui.error("برگرداندن برنامه به حالت قبل انجام نشد. فوراً به تیم فنی اطلاع دهید.")
        raise

    log.info("UPDATE %s result node=%s iis=%s", name, h.node_code, h.iis_code)
    if h.ok or h.node_ok or not rollback.enabled():
        # Node up but IIS failing is a server setting problem: the old files would not fix it.
        status = "success" if h.ok else "failed"
        healthcheck.report(h, name, app)
    else:
        healthcheck.report(h, name, app)
        h2 = rollback.restore(app, tools.nssm)
        if h2 and h2.node_ok:
            status = "rolled_back"
            healthcheck.report(h2, name, app)
            ui.warn("نسخه جدید مشکل داشت، پس برنامه خودکار به نسخه قبلی برگشت و الان کار می‌کند. "
                    "پیام خطای بالا را برای تیم فنی بفرستید.")
        else:
            status = "rollback_failed"
            if h2:
                healthcheck.report(h2, name, app)
            ui.error("برگشت به نسخه قبلی هم موفق نبود. فوراً به تیم فنی اطلاع دهید.")
    registry.upsert(name, last_deploy_at=registry.now(), last_deploy_status=status,
                    log_dir=app["log_dir"], backup_path=app["backup_path"])


# ---------------------------------------------------------------- other menu items
def remove() -> None:
    ui.title("حذف برنامه")
    apps = registry.load()
    if not apps:
        ui.info("هیچ برنامه‌ای برای حذف پیدا نشد.")
        return
    name = pick_app(apps, "کدام برنامه حذف شود؟")
    if name:
        uninstall.run(name, apps[name])


def show_apps(apps: dict) -> None:
    nssm_path = prerequisites.find_nssm()
    for name in sorted(apps, key=str.lower):
        a = apps[name]
        state = nssm.status(nssm_path, a.get("nssm_service") or name) if nssm_path else "?"
        ui.say(f"{name} - پورت {a.get('port')} - {healthcheck.SERVICE_FA.get(state, state)}", "bold")
        last = a.get("last_deploy_status") or ""
        ui.info(f"پوشه: {a.get('deploy_path')} - آخرین نصب: {a.get('last_deploy_at') or '-'} {STATUS_FA.get(last, last)}")


def list_apps() -> None:
    ui.title("فهرست برنامه‌های نصب‌شده")
    with ui.busy("در حال خواندن وضعیت برنامه‌ها..."):
        apps = registry.load()
    if not apps:
        ui.info("هیچ برنامه‌ای ثبت نشده است.")
        return
    show_apps(apps)


def rescan() -> None:
    ui.title("بازبینی سرور و بازسازی فهرست برنامه‌ها")
    with ui.busy("در حال بررسی سرویس‌ها و سایت‌های این سرور..."):
        apps = registry.rescan()
    ui.ok(f"بازبینی تمام شد: {len(apps)} برنامه پیدا شد و فهرست به‌روز شد.")
    show_apps(apps)


MENU = [
    ("نصب برنامه جدید", install),
    ("به‌روزرسانی برنامه نصب‌شده", update),
    ("حذف برنامه", remove),
    ("فهرست برنامه‌های نصب‌شده", list_apps),
    ("بازبینی سرور و بازسازی فهرست برنامه‌ها", rescan),
    ("خروج", None),
]


def main() -> int:
    ui.init_console()
    logger.setup()
    log.info("===== NextDeployer started (pid %s) =====", os.getpid())
    if not prerequisites.is_admin():
        ui.error("این برنامه باید با دسترسی مدیر (Administrator) اجرا شود.")
        ui.info("برنامه را ببندید و دوباره باز کنید؛ اگر ویندوز اجازه خواست، «Yes» را بزنید.")
        ui.pause("برای بستن Enter را بزنید.")
        return 1
    while True:
        ui.title("NextDeployer - نصب و به‌روزرسانی برنامه‌های Next.js")
        label, action = MENU[ui.choose("چه کاری می‌خواهید انجام دهید؟", [m[0] for m in MENU])]
        if action is None:
            log.info("exit")
            return 0
        log.info("menu: %s", action.__name__)
        try:
            action()
        except KeyboardInterrupt:
            log.info("cancelled by operator")
            ui.warn("عملیات لغو شد.")
        except DeployError as e:
            log.error("DeployError: %s", e)
            ui.error(str(e))
        except Exception:
            log.exception("unexpected error")
            ui.error("یک خطای پیش‌بینی‌نشده رخ داد.")
            ui.info(f"جزئیات فنی در این فایل ثبت شد: {config.LOG_FILE}")
            ui.info("لطفاً این فایل را برای تیم فنی بفرستید.")
        ui.pause("برای بازگشت به منوی اصلی Enter را بزنید.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        log.exception("fatal")
        ui.error("یک خطای پیش‌بینی‌نشده رخ داد و برنامه بسته می‌شود.")
        ui.info(f"جزئیات فنی در این فایل ثبت شد: {config.LOG_FILE}")
        ui.pause("برای بستن Enter را بزنید.")
        sys.exit(1)
