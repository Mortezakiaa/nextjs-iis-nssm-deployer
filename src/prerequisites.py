"""Fail-fast checks: nothing on the server is changed until all of these pass. Also auto-configures ARR."""
import ctypes
import glob
import os
import re
import shutil
import winreg
from dataclasses import dataclass

import archive
import config
import iis
import nssm
import registry
import ui
from shell import DeployError, run

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{1,59}$")
HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass
class Tools:
    node: str
    node_version: str
    nssm: str


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _first_file(candidates) -> str:
    return next((os.path.abspath(c) for c in candidates if c and os.path.isfile(c)), "")


def _node_from_registry() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Node.js", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            return os.path.join(winreg.QueryValueEx(k, "InstallPath")[0], "node.exe")
    except OSError:
        return ""


def find_node() -> str:
    # shutil.which covers both `where node` and PATH.
    return _first_file([shutil.which("node"), r"C:\Program Files\nodejs\node.exe", _node_from_registry()])


def find_nssm() -> str:
    return _first_file([
        shutil.which("nssm"),
        r"C:\Program Files\nssm\nssm.exe", r"C:\Program Files\nssm\win64\nssm.exe",
        r"C:\nssm\nssm.exe", r"C:\nssm\win64\nssm.exe",
        *glob.glob(r"C:\nssm*\win64\nssm.exe"), *glob.glob(r"C:\Program Files\nssm*\win64\nssm.exe"),
    ])


def check_server(full: bool = True) -> Tools:
    """full=False is for uninstall: only admin, IIS and NSSM are needed."""
    problems = []
    if not is_admin():
        problems.append("- برنامه با دسترسی مدیر (Administrator) اجرا نشده است. برنامه را ببندید و دوباره باز کنید.")
    node = find_node() if full else ""
    if full and not node:
        problems.append("- Node.js روی این سرور نصب نیست. نسخه LTS را از سایت nodejs.org دانلود و نصب کنید.")
    nssm_path = find_nssm()
    if not nssm_path:
        problems.append("- برنامه NSSM پیدا نشد. آن را از سایت nssm.cc دانلود کنید و فایل nssm.exe "
                        "(از پوشه win64) را در مسیر C:\\Program Files\\nssm قرار دهید.")
    if not config.APPCMD.exists():
        problems.append("- IIS روی این سرور نصب نیست. از Server Manager نقش Web Server (IIS) را اضافه کنید.")
    elif full:
        if not iis.rewrite_installed():
            problems.append("- ماژول URL Rewrite روی IIS نصب نیست. آن را از سایت iis.net (URL Rewrite 2.1) نصب کنید.")
        if iis.proxy_state() is None:
            problems.append("- ماژول ARR (Application Request Routing) روی IIS نصب نیست. "
                            "آن را از سایت iis.net (ARR 3.0) نصب کنید.")
    if problems:
        raise DeployError("\n".join(["پیش‌نیازهای سرور کامل نیست. هیچ تغییری روی سرور داده نشد:", *problems]))

    version = ""
    if full:
        version = run([node, "--version"], 30).out.strip()
        ui.ok(f"Node.js پیدا شد: نسخه {version}")
        if version.lstrip("v").split(".")[0].isdigit() and int(version.lstrip("v").split(".")[0]) < 18:
            ui.warn("نسخه Node.js قدیمی است؛ برنامه‌های Next.js جدید به نسخه 18 یا بالاتر نیاز دارند.")
        changed = iis.ensure_proxy()
        if changed:
            iis.recycle_all_pools()
            ui.ok("تنظیمات پراکسی ARR در IIS فعال شد (تا فرم‌ها و Server Actionها خطای 500 ندهند).")
    ui.ok("پیش‌نیازهای سرور (IIS، NSSM) آماده هستند.")
    return Tools(node, version, nssm_path)


# ---------------------------------------------------------------- answer validators (return a Persian problem or "")
def name_problem(name: str, apps: dict) -> str:
    if not NAME_RE.match(name):
        return "نام فقط می‌تواند حروف انگلیسی، عدد، خط تیره (-) و زیرخط (_) داشته باشد و باید با حرف شروع شود."
    if registry.find(apps, name):
        return "برنامه‌ای با این نام قبلاً نصب شده است. برای نصب نسخه جدید، از گزینه «به‌روزرسانی» در منوی اصلی استفاده کنید."
    lname = name.lower()
    if nssm.exists(name) or any(s.lower() == lname for s in iis.sites()) or any(p.lower() == lname for p in iis.apppools()):
        return ("سرویس یا سایتی با این نام از قبل روی سرور وجود دارد. اگر همین برنامه است، از منوی اصلی "
                "«بازبینی سرور» و بعد «به‌روزرسانی» را بزنید؛ وگرنه نام دیگری انتخاب کنید.")
    return ""


def dest_problem(dest: str, z: archive.ZipSummary) -> str:
    if not re.match(r"^[A-Za-z]:\\", dest):
        return "مسیر باید کامل باشد، مثلاً D:\\Apps\\MyApp"
    if not os.path.splitdrive(dest)[1].strip("\\/"):
        return "نمی‌شود برنامه را مستقیم در ریشه درایو نصب کرد. یک پوشه مشخص کنید، مثلاً D:\\Apps\\MyApp"
    if not os.path.isdir(dest[:3]):
        return f"درایو {dest[:2]} روی این سرور وجود ندارد."
    if os.path.isfile(dest):
        return "این مسیر یک فایل است، نه پوشه."
    if os.path.isdir(dest) and os.listdir(dest):
        return "این پوشه خالی نیست. یک پوشه جدید یا خالی انتخاب کنید."
    if not archive.writable(dest):
        return "اجازه نوشتن در این مسیر وجود ندارد. مسیر دیگری انتخاب کنید."
    if not archive.enough_space(dest, z, with_backup=False):
        return "فضای خالی این درایو کافی نیست. درایو دیگری انتخاب کنید یا فضا آزاد کنید."
    return ""


def clean_host(text: str) -> str:
    host = re.sub(r"^\w+://", "", text.strip().lower()).split("/")[0]
    return host.split(":")[0]


def host_problem(host: str) -> str:
    if not HOST_RE.match(host):
        return "آدرس دامنه درست نیست. نمونه درست: shop.example.com"
    taken = iis.host_taken(host)
    if taken:
        return f"این دامنه قبلاً به سایت «{taken}» در IIS داده شده است."
    return ""
