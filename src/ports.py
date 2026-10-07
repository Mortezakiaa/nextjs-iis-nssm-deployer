"""Port discovery. A port is taken if ANY source claims it: NSSM service env (even when stopped), IIS web.config
rewrite targets, IIS bindings, netstat, Hyper-V/Docker excluded ranges, or our own registry."""
import os
import re
import socket
import winreg
from itertools import count

import iis
from shell import run

SERVICES_KEY = r"SYSTEM\CurrentControlSet\Services"
NSSM_VALUES = ("Application", "AppDirectory", "AppParameters", "AppEnvironment", "AppEnvironmentExtra", "AppStdout")
WEBCONFIG_PORT_RE = re.compile(r'url\s*=\s*"https?://(?:127\.0\.0\.1|localhost|\[::1\]):(\d+)', re.I)
NETSTAT_RE = re.compile(r"^\s*TCP\s+(?:\[[^\]]*\]|[\d.]+|\*):(\d+)\s", re.M | re.I)
EXCLUDED_RE = re.compile(r"^\s*(\d+)\s+(\d+)", re.M)


def nssm_services() -> dict:
    """{service: {nssm parameter: value}} for every NSSM service, read from the registry keys `nssm get` reads.
    Same data as `nssm get <svc> AppEnvironmentExtra`, without spawning nssm per service or decoding its output."""
    found = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, SERVICES_KEY) as root:
        for i in count():
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            try:
                with winreg.OpenKey(root, name) as k:
                    image = str(winreg.QueryValueEx(k, "ImagePath")[0])
            except OSError:
                continue
            if "nssm" not in image.lower():
                continue
            params = {}
            try:
                with winreg.OpenKey(root, name + r"\Parameters") as k:
                    for v in NSSM_VALUES:
                        try:
                            params[v] = winreg.QueryValueEx(k, v)[0]
                        except OSError:
                            pass
            except OSError:
                pass
            found[name] = params
    return found


def env_port(params: dict):
    for key in ("AppEnvironmentExtra", "AppEnvironment"):
        value = params.get(key) or []
        for line in [value] if isinstance(value, str) else value:
            k, _, v = line.partition("=")
            if k.strip().upper() == "PORT" and v.strip().isdigit():
                return int(v)
    return None


def webconfig_ports(folder: str) -> set:
    try:
        with open(os.path.join(folder, "web.config"), encoding="utf-8", errors="replace") as f:
            return {int(p) for p in WEBCONFIG_PORT_RE.findall(f.read())}
    except OSError:
        return set()


def netstat_ports(text: str = None) -> set:
    """Local TCP ports in use (IPv4 and IPv6, any state)."""
    text = run(["netstat", "-ano"]).out if text is None else text
    return {int(p) for p in NETSTAT_RE.findall(text)}


def excluded_ranges(text: str = None) -> list:
    text = run(["netsh", "interface", "ipv4", "show", "excludedportrange", "protocol=tcp"]).out if text is None else text
    return [(int(a), int(b)) for a, b in EXCLUDED_RE.findall(text)]


def taken_ports(apps: dict) -> set:
    taken = {p for p in map(env_port, nssm_services().values()) if p}
    for path in set(iis.vdir_paths().values()):
        taken |= webconfig_ports(path)
    taken |= iis.binding_ports()
    taken |= netstat_ports()
    for a, b in excluded_ranges():
        taken |= set(range(a, b + 1))
    taken |= {a["port"] for a in apps.values() if isinstance(a.get("port"), int)}
    return taken


def can_bind(port: int) -> bool:
    for host in ("127.0.0.1", "0.0.0.0"):
        s = socket.socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
            s.bind((host, port))
        except OSError:
            return False
        finally:
            s.close()
    return True


def pick(lo: int, hi: int, taken: set):
    """Lowest port in [lo, hi] no source claims and that we can actually bind. None if the range is full."""
    return next((p for p in range(lo, hi + 1) if p not in taken and can_bind(p)), None)


def listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False
