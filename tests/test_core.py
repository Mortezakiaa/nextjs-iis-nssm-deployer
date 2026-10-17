"""Self-check for the logic that doesn't need IIS/NSSM. Run: .venv\\Scripts\\python tests\\test_core.py"""
import http.server
import os
import socket
import sys
import tempfile
import threading
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
tmp = tempfile.mkdtemp()
os.environ["PROGRAMDATA"] = tmp  # keep registry/log writes out of the real C:\ProgramData

import archive, config, healthcheck, iis, nssm, ports, prerequisites, registry, ui, webconfig  # noqa: E402
from shell import DeployError, Result, decode  # noqa: E402

# decoding: nssm writes UTF-16LE without BOM, others utf-8 / OEM code page
assert decode("SERVICE_RUNNING\r\n".encode("utf-16-le")).strip() == "SERVICE_RUNNING"
assert decode(b"\xff\xfe" + "سلام".encode("utf-16-le")) == "سلام"
assert decode("پورت".encode("utf-8")) == "پورت"
assert decode(bytes([0xC7, 0xE1, 0xDD])) != ""  # cp1256 bytes never crash

# parsers
assert iis.parse_bindings("http/*:80:A.com,https/*:443:a.com,net.tcp/808:*,http/[::1]:8001:") == [
    ("http", "*", 80, "a.com"), ("https", "*", 443, "a.com"), ("http", "[::1]", 8001, "")]
# IIS port conflicts: same port + same domain, or the port used for non-http; different domains may share a port
fake_sites = {"Default Web Site": {"bindings": "http/*:80:"}, "Shop": {"bindings": "http/*:80:shop.com,https/*:443:shop.com"}}
assert iis.binding_owner(80, "", fake_sites) == "Default Web Site"
assert iis.binding_owner(80, "Shop.com", fake_sites) == "Shop"
assert iis.binding_owner(80, "blog.com", fake_sites) == ""
assert iis.binding_owner(443, "blog.com", fake_sites) == "Shop"
assert iis.binding_owner(8080, "", fake_sites) == ""
# allowed server variables, as `appcmd list config -section:system.webServer/rewrite/allowedServerVariables` prints them
allowed_xml = "<system.webServer>\r\n  <rewrite>\r\n    <allowedServerVariables{}\r\n  </rewrite>\r\n</system.webServer>\r\n"
assert iis.parse_allowed_server_variables("") == set()
assert iis.parse_allowed_server_variables(allowed_xml.format(" />")) == set()
assert iis.parse_allowed_server_variables(allowed_xml.format(
    '>\r\n      <add name="HTTP_X_FORWARDED_HOST" />\r\n    </allowedServerVariables>')) == {"HTTP_X_FORWARDED_HOST"}
assert iis.parse_allowed_server_variables(allowed_xml.format(
    '>\r\n      <add name="http_x_forwarded_host" />\r\n      <add name="HTTP_X_Forwarded_Proto" />\r\n'
    '    </allowedServerVariables>')) == {"HTTP_X_FORWARDED_HOST", "HTTP_X_FORWARDED_PROTO"}
# allow_server_variables only adds what's missing (`/+` on an existing name fails), once per name
calls, real_appcmd = [], iis.appcmd


def fake_appcmd(*args, timeout=120):
    calls.append(args)
    if args[0] == "list":
        return Result(0, allowed_xml.format('>\r\n      <add name="http_x_forwarded_host" />\r\n    </allowedServerVariables>'), "")
    return Result(0, "", "")


iis.appcmd = fake_appcmd
assert iis.allow_server_variables(["HTTP_X_FORWARDED_HOST", "HTTP_X_FORWARDED_PROTO", "http_x_forwarded_proto"]) == [
    "HTTP_X_FORWARDED_PROTO"]
assert [c[0] for c in calls] == ["list", "set"] and calls[1][3] == "/+[name='HTTP_X_FORWARDED_PROTO']"
assert iis.allow_server_variables(["HTTP_X_FORWARDED_HOST"]) == [] and [c[0] for c in calls] == ["list", "set", "list"]
iis.appcmd = real_appcmd
netstat = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1012
  TCP    127.0.0.1:3001         0.0.0.0:0              LISTENING       4410
  TCP    [::]:3005              [::]:0                 LISTENING       4
  TCP    [fe80::1%5]:3007       [::]:0                 LISTENING       4
  UDP    0.0.0.0:3009           *:*                                    999
"""
assert ports.netstat_ports(netstat) == {135, 3001, 3005, 3007}
excluded = """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      3050        3149
     50000       50059     *
"""
assert ports.excluded_ranges(excluded) == [(3050, 3149), (50000, 50059)]
assert ports.env_port({"AppEnvironmentExtra": ["NODE_ENV=production", "PORT=3002"]}) == 3002
assert ports.env_port({"AppEnvironment": "PORT=3003"}) == 3003
assert ports.env_port({}) is None
# service env: ours win, the team's extra variables survive an update
assert nssm.env_args(3005, ["PORT=3001", "DATABASE_URL=x=y", "hostname=0.0.0.0"]) == [
    "PORT=3005", "NODE_ENV=production", "HOSTNAME=127.0.0.1", "DATABASE_URL=x=y"]
assert nssm.env_args(3005, "PORT=1") == ["PORT=3005", "NODE_ENV=production", "HOSTNAME=127.0.0.1"]

# web.config: generated, valid, and discoverable by the port scanner
d = tempfile.mkdtemp()
webconfig.write(d, 3123)
assert webconfig.validate(os.path.join(d, "web.config"), 3123)
assert not webconfig.validate(os.path.join(d, "web.config"), 3124)
assert ports.webconfig_ports(d) == {3123}
# template: valid XML that forwards the original host and scheme to Node
ET.fromstring(webconfig.render(3123))
assert 'value="{MapProto:{HTTPS}}"' in webconfig.render(3123)
assert webconfig.server_variables(os.path.join(d, "web.config")) == ["HTTP_X_FORWARDED_HOST", "HTTP_X_FORWARDED_PROTO"]
# the app's own web.config: its variables are read at any depth; unreadable -> none
other = os.path.join(tempfile.mkdtemp(), "web.config")
with open(other, "w", encoding="utf-8") as f:
    f.write('<configuration><location path="api"><system.webServer><rewrite><rules><rule name="x">'
            '<serverVariables><set name="HTTP_X_ORIGINAL_HOST" value="{HTTP_HOST}" /></serverVariables>'
            '</rule></rules></rewrite></system.webServer></location></configuration>')
assert webconfig.server_variables(other) == ["HTTP_X_ORIGINAL_HOST"]
with open(other, "w", encoding="utf-8") as f:
    f.write("<configuration>")
assert webconfig.server_variables(other) == [] and webconfig.server_variables(other + ".missing") == []
# existing web.config: only the port changes, the team's edits and line endings stay
custom = webconfig.render(3123).replace("<handlers>", "<!-- team edit -->\n    <handlers>").replace("\n", "\r\n")
with open(os.path.join(d, "web.config"), "w", encoding="utf-8", newline="") as f:
    f.write(custom)
webconfig.write(d, 3200)
with open(os.path.join(d, "web.config"), encoding="utf-8", newline="") as f:
    assert f.read() == custom.replace("127.0.0.1:3123/", "127.0.0.1:3200/")
# existing web.config without that line: never replaced by a new one
with open(os.path.join(d, "web.config"), "w", encoding="utf-8") as f:
    f.write("<configuration />")
try:
    webconfig.write(d, 3200)
    raise AssertionError("expected DeployError")
except DeployError:
    pass
with open(os.path.join(d, "web.config"), encoding="utf-8") as f:
    assert f.read() == "<configuration />"
assert 'existingResponse="PassThrough"' in webconfig.render(1, 5) and 'maxAllowedContentLength="5"' in webconfig.render(1, 5)

# port picking skips taken and actually-bound ports
s = socket.socket()
s.bind(("127.0.0.1", 0))
s.listen()
busy = s.getsockname()[1]
assert ports.pick(busy, busy, set()) is None
assert ports.pick(busy - 2, busy + 2, {busy - 2, busy - 1}) not in (busy - 2, busy - 1, busy)
assert ports.listening("127.0.0.1", busy)
s.close()


# zip: single top-level folder is flattened, missing server.js is refused
def make_zip(path, files):
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


zp = make_zip(os.path.join(tmp, "nested.zip"), {"standalone/server.js": "x", "standalone/.next/static/a.css": "y",
                                                "__MACOSX/._x": ""})
info = archive.inspect(zp)
assert info.prefix == "standalone/" and info.has_static
dest = os.path.join(tmp, "deep", "App")
archive.move_contents(archive.stage(info, dest), dest)
archive.rmtree(archive.staging_dir(dest))
assert os.path.isfile(os.path.join(dest, "server.js")) and not os.path.exists(archive.staging_dir(dest))
archive.backup(dest, dest + "_backup")
archive.clear(dest)
assert os.listdir(dest) == []
archive.restore(dest + "_backup", dest)
assert os.path.isfile(os.path.join(dest, ".next", "static", "a.css"))

# non-empty folder (operator put the zip + the tool in it): same-named entries replaced, theirs kept everywhere
for keep in ("default-standalone.zip", archive.SELF_NAME, "notes.txt"):
    open(os.path.join(dest, keep), "w").close()
archive.move_contents(archive.stage(info, dest), dest)  # .next and server.js already exist
assert os.path.isfile(os.path.join(dest, "notes.txt"))
archive.backup(dest, dest + "_backup")
assert sorted(os.listdir(dest + "_backup")) == [".next", "notes.txt", "server.js"]
open(os.path.join(dest, "web.config"), "w").close()
archive.clear(dest)
assert sorted(os.listdir(dest)) == sorted(["default-standalone.zip", archive.SELF_NAME, "web.config"])
archive.restore(dest + "_backup", dest)
assert os.path.isfile(os.path.join(dest, "server.js")) and os.path.isfile(os.path.join(dest, "default-standalone.zip"))
for bad in ({"a/server.js": "", "b/x": ""}, {"deep/er/server.js": ""}):
    try:
        archive.inspect(make_zip(os.path.join(tmp, "bad.zip"), bad))
        raise AssertionError("expected DeployError")
    except DeployError:
        pass
assert archive.inspect(make_zip(os.path.join(tmp, "flat.zip"), {"server.js": ""})).prefix == ""

# registry: atomic save/load round trip, broken file set aside
registry.save({"A": registry.new_entry("A", 3001, r"I:\A", "node.exe", r"C:\logs\A")})
registry.upsert("A", last_deploy_status="success")
assert registry._read()["A"]["last_deploy_status"] == "success" and registry.find(registry._read(), "a") == "A"
config.REGISTRY_FILE.write_text("{broken", "utf-8")
assert registry._read() is None and not config.REGISTRY_FILE.exists()

# validators
assert prerequisites.clean_host("https://Shop.Example.com:443/path") == "shop.example.com"
assert prerequisites.HOST_RE.match("a.b-c.com") and not prerequisites.HOST_RE.match("-a.com")
assert prerequisites.NAME_RE.match("LandingPage") and not prerequisites.NAME_RE.match("1app")
assert not prerequisites.NAME_RE.match("برنامه")


# health check: any non-5xx answer counts, redirects are not followed, dead port is None
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(307 if self.path == "/r" else 200)
        self.send_header("Location", "https://example.invalid/")
        self.end_headers()

    def log_message(self, *a):
        pass


srv = http.server.HTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
p = srv.server_address[1]
assert healthcheck.http_status(f"http://127.0.0.1:{p}/") == 200
assert healthcheck.http_status(f"http://127.0.0.1:{p}/r") == 307
srv.shutdown()
srv.server_close()
assert healthcheck.http_status(f"http://127.0.0.1:{p}/", timeout=1) is None

# Persian shaping never crashes and keeps embedded paths readable
assert "I:\\App" in ui.fa("پوشه نصب: I:\\App")

print("all checks passed")
