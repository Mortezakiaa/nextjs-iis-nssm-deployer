# NextDeployer

**English** | [فارسی](README.fa.md)

A single-file Windows tool (`NextDeployer.exe`) that installs, updates and removes Next.js apps on Windows Server behind IIS. There is nothing to install: double-click it, accept the Administrator (UAC) prompt, and it walks the operator through each task step by step.

> The tool's user interface is in **Persian (Farsi)**.

How each app runs on the server:

```
Browser  →  IIS site (URL Rewrite + ARR)  →  Node.js app running as a Windows service (NSSM)
```

Each app gets its own internal port (3001, 3002, ...), listens only on `127.0.0.1`, and can be reached only through IIS.

---

## Important notes (read before first use)

1. **Prerequisites are checked, not installed.** IIS, URL Rewrite, ARR, Node.js and NSSM must already be on the server (see [Server prerequisites](#1-server-prerequisites-one-time)). If anything is missing, the tool stops before changing the server.
2. **The tool does not build your app.** It deploys a ready-made zip of the Next.js `standalone` output (see [Preparing the Next.js build](#preparing-the-nextjs-build)). `NEXT_PUBLIC_*` variables are baked in at build time, so set them before `next build`.
3. **Updates replace the whole app folder.** On update, everything in the app folder is deleted and replaced with the new zip's contents. Only `*.zip` files and `NextDeployer.exe` are kept. Never store uploads, user data, SQLite files or hand-edited config inside the app folder. Keep them in a separate folder, or ship them inside the zip. This also applies to files that were already in the folder when you first installed into it.
4. **Only one backup is kept.** Before each update, the current version is copied to `<app folder>_backup`, overwriting the previous backup. You can roll back one version only.
5. **Free disk space.** An update needs roughly twice the unpacked app size free on the drive: one copy for the new version and one for the backup.
6. **Short downtime on update.** The site is unavailable for a few seconds while the service restarts.
7. **Environment variables (e.g. `DATABASE_URL`) are set on the service, not by the tool.** After the first install, add them with `nssm edit <AppName>` (Environment tab), then run `nssm restart <AppName>`. The tool keeps them across updates. Do not set `PORT`, `HOSTNAME` or `NODE_ENV` there: the tool manages those and overwrites them.
8. **Server-wide ARR change.** The tool enables the ARR proxy and sets `preserveHostHeader`, `reverseRewriteHostInResponseHeaders` and a 2-minute timeout at the **server level** (`system.webServer/proxy`). When it changes these settings, it recycles all running application pools. If other sites on the same server use ARR, check that they still work.
9. **HTTP only.** The tool creates an `http` binding. For HTTPS, add the certificate and an `https` binding in IIS Manager. If the site uses a port other than 80, open that port in Windows Firewall. Domains need a DNS record pointing to the server.
10. **Service account.** Services are created with the NSSM default account (LocalSystem). They start automatically with Windows and restart 5 seconds after a crash. To use a less privileged account, change it with `nssm edit <AppName>` (Log on tab).
11. **Unsigned executable.** `NextDeployer.exe` is built with PyInstaller and is not code-signed. Windows SmartScreen or antivirus software may warn about it. You can build it yourself from source (see [For developers](#6-for-developers)).
12. **Use the tool for every change.** Don't rename or delete the app's IIS site, app pool or NSSM service by hand. The tool keeps its own list in `C:\ProgramData\NextDeployer\apps.json`. It rebuilds that list from the server if the file is lost, but manual renames break the link.

---

## 1. Server prerequisites (one-time)

Before doing anything, the tool checks all of these. If something is missing, it stops **without changing the server** and says what is missing.

| Prerequisite | How to install |
|---|---|
| **IIS** | Server Manager → Add Roles and Features → **Web Server (IIS)** role. If your apps need WebSockets, also select **WebSocket Protocol**. |
| **URL Rewrite 2.1** | Download and install from iis.net. |
| **ARR 3.0** (Application Request Routing) | Download and install from iis.net. **No need to enable the proxy yourself**: the tool enables and configures it. |
| **Node.js** (LTS, 18 or later) | Install the Windows Installer (.msi) from nodejs.org. The tool finds it automatically. |
| **NSSM** | Download the zip from nssm.cc and copy `win64\nssm.exe` to `C:\Program Files\nssm\nssm.exe`. (`C:\nssm\...` and `PATH` are also searched.) |

The tool must run as Administrator. It requests this itself via UAC.

### Preparing the Next.js build

Set `output: 'standalone'` in `next.config.js`. After `next build`:

1. Copy `.next/static` to `.next/standalone/.next/static`.
2. Copy `public` (if it exists) to `.next/standalone/public`.
3. Zip the **contents** of `.next/standalone`. `server.js` must be at the root of the zip, or inside a single top-level folder.

---

## 2. Operator guide

1. Download `NextDeployer.exe` from the [latest release](https://github.com/Mortezakiaa/nextjs-iis-nssm-deployer/releases/latest), copy it to the server and double-click it.
2. When Windows asks whether to allow the app to make changes, click **Yes**.
3. In the main menu, type the number of an option and press Enter.

> Tip: to enter a file or folder path, you can drag it into the window.

### Option 1: Install a new app

The tool asks only for:

1. **Path to the app's zip.** It checks that the zip is valid and contains `server.js`.
2. **App name** in English (e.g. `LandingPage`). The same name is used for the Windows service and the IIS site.
3. **Install folder** on the server (e.g. `D:\Apps\LandingPage`). The folder doesn't have to be empty: files with the same names are replaced and other files are left alone during install. They will be removed by the first update (see Important notes, item 3). A folder that belongs to another app on the server is rejected.
4. **Site domain** (e.g. `shop.example.com`). Press Enter if you don't have one yet.
5. **IIS site port** (usually `80` for a site with a domain). Several sites with different domains can share a port. If the port is already used by a site with the same domain, or by another program, the tool asks for a different one.

The app's internal (NSSM) port is chosen **automatically** and shown for information. After you confirm, the tool runs these steps:
check prerequisites → reserve port → unpack zip → write web.config → create IIS site → create Windows service → start → health check → save to app list.

If any step fails, the tool undoes all partial changes so the server stays clean.

> If the name is already taken, the tool refuses the install and points you to the Update option.

### Option 2: Update an installed app

1. Pick the app from the list.
2. Enter the path to the new zip.
3. Confirm.

Port and folder are not asked again. The tool unpacks the new version next to the running one, backs up the current version to `<folder>_backup`, stops the service and waits until it has fully exited, replaces the files, starts the service, and runs a health check.

**If the new version doesn't come up, the tool automatically rolls back to the previous version** and reports the result. Just send the error message to your technical team.

### Option 3: Remove an app

Pick the app from the list. The tool shows exactly what will be removed and asks separately:
- Delete the app folder and its backup?
- Delete the log folder?

After final confirmation, it stops and removes the Windows service, removes the IIS site and app pool, and frees the port. Anything that was already removed by hand is skipped without an error.

### Option 4: List installed apps

Shows each app's name, port, current status and last deployment time.

---

## 3. Health check results

| Result | Meaning | What to do |
|---|---|---|
| App started and is healthy | Everything works. The site URL is shown. | Nothing. |
| App is healthy, the problem is the IIS/ARR configuration | The app works, but IIS doesn't forward requests to it. | Check the items listed on screen (see Troubleshooting). |
| The Node app itself didn't start | The app has an error. Its latest error output is shown. | Send that error text to the development team. |
| Security warning: the app is directly reachable from outside | The app listens on all addresses instead of `127.0.0.1`. | Tell the development team (the code must not override `HOSTNAME`). |

---

## 4. Troubleshooting

### Error 500 when submitting forms (Server Actions)
Cause: ARR rewrites the `Host` header to the internal address (`127.0.0.1:3001`), and Next.js rejects the request as CSRF.
Fix: on every run, the tool sets `preserveHostHeader` and the other proxy settings. If you still see the error, run `iisreset` once in an Administrator Command Prompt. To check manually:
```cmd
%windir%\system32\inetsrv\appcmd.exe list config -section:system.webServer/proxy
```
You should see `enabled="true"` and `preserveHostHeader="true"`.

### Port conflicts
To find a free port, the tool checks all of these together: every NSSM service's configuration (including stopped ones), every IIS site's web.config, IIS bindings, ports in use (`netstat`), Hyper-V/Docker reserved ranges, and its own app list. It then test-binds the port.
If you still get a conflict (for example, another program took the port later), tell your technical team. The port range can be changed in `settings.json` (below).

### The service doesn't start
- On failure, the tool shows the last 20 lines of the app's error log.
- Full app logs: `C:\ProgramData\NextDeployer\logs\<AppName>\err.log` and `out.log`.
- Common causes: an incomplete zip (`node_modules` missing from standalone), missing environment variables the app needs (e.g. a database URL), or an old Node.js version.
- A "paused" status means the app keeps crashing and NSSM restarts it every 5 seconds.

### The site doesn't open by its domain name
- If the tool says the domain isn't pointing to the server yet (DNS), the domain's DNS record must point to this server's IP.
- If the site uses a port other than 80 (e.g. 8080), that port must be open in Windows Firewall.
- The tool creates only an `http` binding. For `https`, add the certificate to the site in IIS Manager (Bindings → Add → https).

### The site looks broken (CSS/JS/images don't load)
`.next/static` and `public` are missing from the zip. The tool warns about this when it checks the zip. Ask the development team to copy both folders into standalone as described in [Preparing the Next.js build](#preparing-the-nextjs-build) and zip it again.

---

## 5. Files and settings

| Path | Description |
|---|---|
| `C:\ProgramData\NextDeployer\apps.json` | The app list (the source of truth for update and remove). If it is deleted or corrupted, the tool rebuilds it from the NSSM services and IIS sites on the next run. |
| `C:\ProgramData\NextDeployer\deployer.log` | The tool's full technical log: every command, parameter and result. Send this file to your technical team if something goes wrong. |
| `C:\ProgramData\NextDeployer\logs\<AppName>\` | The app's own output and errors (`out.log`, `err.log`), rotated at 10 MB. |
| `C:\ProgramData\NextDeployer\settings.json` | Optional settings (create it only if needed). |

Example `settings.json` (all keys are optional; the values shown are the defaults):

```json
{
  "port_min": 3001,
  "port_max": 3999,
  "logs_root": "C:\\ProgramData\\NextDeployer\\logs",
  "max_upload_mb": 100,
  "websockets": false,
  "rollback": true,
  "health_timeout": 60,
  "health_interval": 2
}
```

- `rollback`: `false` turns off automatic rollback after a failed update (the backup is still made).
- `websockets`: `true` enables WebSocket support in web.config (requires the IIS WebSocket Protocol feature).
- `max_upload_mb`: the maximum request/upload size IIS accepts.

### If Persian text doesn't display correctly
In the classic Windows console (conhost), the tool sets the font to Courier New and reorders text right-to-left. If your terminal already handles right-to-left text and the letters appear reversed, set the environment variable `NEXTDEPLOYER_TEXT=raw` before running the tool.

---

## 6. For developers

Code layout:

```
src/
  main.py            entry point, menu, install/update flows
  ui.py              all Persian messages and prompts, colors, RTL display
  prerequisites.py   prerequisite checks + automatic ARR configuration
  ports.py           free-port search (NSSM + web.config + bindings + netstat + reserved ranges)
  registry.py        atomic read/write of apps.json and rebuilding it from the server
  iis.py             appcmd wrappers: sites, app pools, proxy settings
  nssm.py            nssm wrappers: install, configure, start/stop, remove, status
  webconfig.py       web.config from a full template only if missing, else only its port is updated + validation
  archive.py         zip validation and extraction, backup, file replacement (long paths)
  healthcheck.py     health check and result interpretation
  rollback.py        automatic rollback (standalone module, can be disabled)
  uninstall.py       full app removal
  logger.py          the tool's technical log
  config.py          constants and default settings
  shell.py           runs external commands with safe output decoding (UTF-16/UTF-8/cp1256/...)
tests/test_core.py   logic self-check, no IIS/NSSM required
build.py             builds the executable
```

Build the executable (Python 3.11 or later):

```cmd
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python build.py
```

Output: `dist\NextDeployer.exe`, a single file built with `--uac-admin` so it asks for Administrator rights on launch. `build.py` runs `tests/test_core.py` first and refuses to build if it fails.

Note: NSSM service settings are read directly from the registry (`HKLM\SYSTEM\CurrentControlSet\Services\<name>\Parameters`), the same place `nssm get` reads from, without starting a separate nssm process for each service.
