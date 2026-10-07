"""Post-deploy health check: Node directly, then through IIS, then make sure Node is NOT reachable from outside."""
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

import config
import iis
import nssm
import ports
import ui
from shell import decode

SERVICE_FA = {"SERVICE_RUNNING": "در حال اجرا", "SERVICE_STOPPED": "متوقف",
              "SERVICE_PAUSED": "متوقف موقت (برنامه مدام خطا می‌دهد و دوباره اجرا می‌شود)",
              "SERVICE_START_PENDING": "در حال روشن شدن", "SERVICE_STOP_PENDING": "در حال خاموش شدن", "": "پیدا نشد"}
SITE_FA = {"started": "روشن", "stopped": "خاموش", "": "پیدا نشد"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is a valid answer; don't follow it to https/other hosts


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def http_status(url: str, host: str = "", timeout: int = 5):
    req = urllib.request.Request(url, headers={"Host": host} if host else {})
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def poll(url: str, host: str, seconds: int):
    deadline = time.time() + seconds
    while True:
        code = http_status(url, host)
        if (code is not None and code < 500) or time.time() >= deadline:
            return code
        time.sleep(config.HEALTH_INTERVAL)


def local_ips() -> list:
    try:
        ips = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        ips = set()
    return sorted(ip for ip in ips if not ip.startswith("127."))


@dataclass
class Health:
    node_code: object = None
    iis_code: object = None
    url: str = ""
    binding: str = ""
    exposed: list = field(default_factory=list)
    service: str = ""
    site_state: str = ""
    dns_ok: object = None   # None = no domain

    @property
    def node_ok(self) -> bool:
        return self.node_code is not None and self.node_code < 500

    @property
    def iis_ok(self) -> bool:
        return self.iis_code is not None and self.iis_code < 500

    @property
    def ok(self) -> bool:
        return self.node_ok and self.iis_ok


def check(app: dict, nssm_path: str) -> Health:
    h = Health()
    port, site = app["port"], app.get("iis_site")
    with ui.busy("در حال بررسی سلامت برنامه (حداکثر حدود یک دقیقه)..."):
        h.node_code = poll(f"http://127.0.0.1:{port}/", "", config.HEALTH_TIMEOUT)
        b = iis.http_binding(site) if site else None
        if b:
            bport, host = b
            suffix = "" if bport == 80 else f":{bport}"
            h.binding = f"http://{host or '*'}{suffix}"
            h.url = f"http://{host or (local_ips() or ['localhost'])[0]}{suffix}/"
            if h.node_ok:
                h.iis_code = poll(f"http://127.0.0.1:{bport}/", f"{host}{suffix}" if host else "", 20)
            if host:
                try:
                    socket.gethostbyname(host)
                    h.dns_ok = True
                except OSError:
                    h.dns_ok = False
        if h.node_ok:
            h.exposed = [ip for ip in local_ips() if ports.listening(ip, port)]
        h.service = nssm.status(nssm_path, app["nssm_service"])
        h.site_state = iis.site_state(site) if site else ""
    return h


def tail(path: str, n: int = 20) -> list:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 64 * 1024))
            return decode(f.read()).splitlines()[-n:]
    except OSError:
        return []


def show_app_log(app: dict) -> None:
    """Show the app's own error output so the operator never has to look for a log file."""
    log_dir = app.get("log_dir") or ""
    for name, caption in (("err.log", "آخرین خطاهای ثبت‌شده برنامه"), ("out.log", "آخرین پیام‌های برنامه")):
        lines = tail(os.path.join(log_dir, name))
        if lines:
            ui.box(f"{caption} ({name})", lines)
            return
    ui.info("برنامه هیچ پیام خطایی ثبت نکرده است.")


def report(h: Health, name: str, app: dict) -> None:
    ui.console.print()
    if h.ok:
        ui.ok("برنامه با موفقیت راه‌اندازی شد و سالم است.")
        ui.info(f"نام برنامه: {name}")
        ui.info(f"پورت داخلی: {app['port']}")
        ui.info(f"آدرس سایت: {h.url}")
    elif h.node_ok:
        ui.error("اپلیکیشن سالم بالا آمده، مشکل از تنظیمات IIS/ARR است.")
        ui.info("این موارد را بررسی کنید (یا به تیم فنی اطلاع دهید):")
        if not h.binding:
            ui.info("- سایت در IIS هیچ آدرس http ندارد (Binding).")
        else:
            ui.info(f"- آدرس (Binding) سایت در IIS: {h.binding}")
        ui.info("- پراکسی ARR در IIS فعال باشد. اگر همین الان فعال شده، یک بار IIS را ری‌استارت کنید (iisreset).")
        ui.info(f"- فایل web.config در پوشه برنامه باید به پورت {app['port']} اشاره کند.")
        ui.info(f"- سایت و Application Pool آن در IIS روشن باشند. وضعیت سایت: {SITE_FA.get(h.site_state.lower(), h.site_state)}")
        if h.iis_code:
            ui.info(f"- کد پاسخ IIS: {h.iis_code}")
    else:
        ui.error("خود اپلیکیشن Node بالا نیامده.")
        if h.node_code:
            ui.info(f"برنامه با خطای {h.node_code} پاسخ داد.")
        show_app_log(app)
    ui.info(f"وضعیت سرویس ویندوز: {SERVICE_FA.get(h.service, h.service)}")
    ui.info(f"وضعیت سایت در IIS: {SITE_FA.get(h.site_state.lower(), h.site_state)}")
    if h.exposed:
        ui.warn(f"هشدار امنیتی: برنامه از بیرون هم مستقیم روی پورت {app['port']} در دسترس است "
                f"({', '.join(h.exposed)}). به تیم فنی اطلاع دهید.")
    if h.dns_ok is False:
        ui.warn("دامنه این سایت هنوز به سرور وصل نشده است (DNS). تا وقتی تنظیم نشود، سایت با نام دامنه باز نمی‌شود.")
