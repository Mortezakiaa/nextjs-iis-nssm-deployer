"""appcmd.exe wrappers: sites, app pools, server-level ARR proxy config."""
import os
import re
import xml.etree.ElementTree as ET

import config
from shell import check, run


def appcmd(*args, timeout: int = 120):
    return run([config.APPCMD, *args], timeout)


def _list(kind: str) -> list:
    """`appcmd list <kind> /xml` -> list of attribute dicts. Empty when IIS is missing."""
    if not config.APPCMD.exists():
        return []
    r = appcmd("list", kind, "/xml")
    if not r.ok:
        return []
    try:
        return [dict(e.attrib) for e in ET.fromstring(r.out)]
    except ET.ParseError:
        return []


def sites() -> dict:
    return {s["SITE.NAME"]: s for s in _list("site") if "SITE.NAME" in s}


def apppools() -> dict:
    return {p["APPPOOL.NAME"]: p for p in _list("apppool") if "APPPOOL.NAME" in p}


def vdir_paths() -> dict:
    """{vdir name: physical path} for every virtual directory on the server."""
    return {v["VDIR.NAME"]: os.path.expandvars(v.get("physicalPath", ""))
            for v in _list("vdir") if "VDIR.NAME" in v}


def site_root_paths() -> dict:
    """{site name: physical path of the site root}."""
    return {name[:-1]: path for name, path in vdir_paths().items() if name.endswith("/") and name.count("/") == 1}


def parse_bindings(text: str) -> list:
    """'http/*:80:a.com,https/*:443:a.com' -> [('http', '*', 80, 'a.com'), ...]"""
    out = []
    for b in (text or "").split(","):
        proto, _, info = b.strip().partition("/")
        parts = info.rsplit(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            out.append((proto.lower(), parts[0], int(parts[1]), parts[2].lower()))
    return out


def binding_ports() -> set:
    return {b[2] for s in sites().values() for b in parse_bindings(s.get("bindings"))}


def http_binding(site: str):
    """(port, host) of the site's first http binding, or None."""
    s = sites().get(site)
    for proto, _, port, host in parse_bindings(s.get("bindings") if s else ""):
        if proto == "http":
            return port, host
    return None


def binding_owner(port: int, host: str, all_sites: dict = None) -> str:
    """Name of the site already answering http://host:port (or using the port for https etc.), or ''.
    Sites may share a port when their domains differ."""
    for name, s in (sites() if all_sites is None else all_sites).items():
        for proto, _, p, h in parse_bindings(s.get("bindings")):
            if p == port and (proto != "http" or h == host.lower()):
                return name
    return ""


def site_state(site: str) -> str:
    return sites().get(site, {}).get("state", "")


def create_site(name: str, path: str, port: int, host: str) -> None:
    # Proxy-only site: no managed code needed in the pool.
    check(appcmd("add", "apppool", f"/name:{name}", "/managedRuntimeVersion:", "/managedPipelineMode:Integrated"),
          "ساخت Application Pool در IIS انجام نشد.")
    check(appcmd("add", "site", f"/name:{name}", f"/physicalPath:{path}", f"/bindings:http/*:{port}:{host}"),
          "ساخت سایت در IIS انجام نشد.")
    check(appcmd("set", "app", f"{name}/", f"/applicationPool:{name}"),
          "اتصال سایت به Application Pool انجام نشد.")
    appcmd("start", "site", f"/site.name:{name}")  # errors if already started; that's fine


def delete_site(name: str) -> bool:
    """False if there was no such site; raises if IIS refused to delete it."""
    if name not in sites():
        return False
    check(appcmd("delete", "site", f"/site.name:{name}"), "حذف سایت از IIS انجام نشد.")
    return True


def delete_apppool(name: str) -> bool:
    if name not in apppools():
        return False
    check(appcmd("delete", "apppool", f"/apppool.name:{name}"), "حذف Application Pool از IIS انجام نشد.")
    return True


def rewrite_installed() -> bool:
    r = appcmd("list", "config", "-section:system.webServer/globalModules")
    return r.ok and "rewritemodule" in r.out.lower()


def proxy_state():
    """Current ARR proxy attributes, or None when ARR is not installed (section unknown)."""
    r = appcmd("list", "config", "-section:system.webServer/proxy", "/config:*")
    if not r.ok:
        return None
    m = re.search(r"<proxy\b([^>]*)>", r.out)
    return dict(re.findall(r'(\w+)="([^"]*)"', m.group(1))) if m else {}


def ensure_proxy() -> list:
    """Apply the proxy settings Next.js needs. Returns the attribute names that were changed."""
    current = proxy_state() or {}
    changed = [k for k, v in config.PROXY_SETTINGS.items() if current.get(k, "").lower() != v.lower()]
    for k in changed:
        check(appcmd("set", "config", "-section:system.webServer/proxy", f"/{k}:{config.PROXY_SETTINGS[k]}",
                     "/commit:apphost"),
              "فعال‌سازی تنظیمات ARR در IIS انجام نشد.")
    return changed


def parse_allowed_server_variables(text: str) -> set:
    """`appcmd list config -section:system.webServer/rewrite/allowedServerVariables` -> upper-case names
    (IIS compares them case-insensitively)."""
    return {n.upper() for n in re.findall(r'<add\b[^>]*?\bname="([^"]*)"', text or "", re.I)}


def allow_server_variables(names) -> list:
    """Allow, server-wide, the server variables a web.config sets (else every request gets 500.50).
    Only missing names are added: `/+` on an existing one fails. Returns the names that were added."""
    if not names:
        return []
    section = "-section:system.webServer/rewrite/allowedServerVariables"
    allowed = parse_allowed_server_variables(
        check(appcmd("list", "config", section), "خواندن تنظیمات URL Rewrite در IIS انجام نشد.").out)
    added = []
    for name in names:
        if name.upper() not in allowed:
            # no double quotes: those are only for cmd, run() passes the argument as is
            check(appcmd("set", "config", section, f"/+[name='{name}']", "/commit:apphost"),
                  "مجاز کردن متغیرهای سرور در URL Rewrite انجام نشد.")
            allowed.add(name.upper())
            added.append(name)
    return added


def recycle_all_pools() -> None:
    """Gentle alternative to iisreset: overlapped recycle of every running pool so proxy changes take effect."""
    for name, p in apppools().items():
        if p.get("state", "").lower() == "started":
            appcmd("recycle", "apppool", f"/apppool.name:{name}")
