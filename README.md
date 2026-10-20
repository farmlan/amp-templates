# FarmLAN AMP Templates

Custom AMP Generic Module templates for FarmLAN game servers.

---

## Natural Selection 2

### Overview
- **Platform:** Linux only
- **SteamCMD App ID:** 4940
- **Requires Steam login:** Yes (NS2 does not support anonymous download)
- **Web Admin:** Yes, accessible via browser at `http://<server-ip>:<webadminport>/index.html`

---

### Prerequisites

Before creating an NS2 instance in AMP, ensure the target VM has sufficient disk space. NS2 server files are approximately **4GB**.

---

### Creating the Instance

1. In AMP, go to **Create New Instance**
2. Select **Natural Selection 2** from the application dropdown
3. Choose your target (AMPHost01)
4. Set an instance name (e.g. `NS201`)
5. Click **Create**

---

### First-Time Setup (Required After Instance Creation)

NS2 requires a wrapper script to launch correctly. After creating the instance but **before starting it for the first time**, SSH into the target VM and run:

```bash
# Replace NS201 with your actual instance name
mkdir -p /home/amp/.ampdata/instances/NS201/ns2/config
mkdir -p /home/amp/.ampdata/instances/NS201/ns2/logs

cat > /home/amp/.ampdata/instances/NS201/ns2/start.sh << 'EOF'
#!/bin/bash
cd /AMP/ns2/4940
exec x64/server_linux "$@"
EOF

chmod +x /home/amp/.ampdata/instances/NS201/ns2/start.sh
chown amp:amp /home/amp/.ampdata/instances/NS201/ns2/start.sh
```

---

### First Update / Install

1. In AMP, click **Update** on the NS2 instance
2. AMP will prompt for Steam credentials — NS2 requires a valid Steam account to download
3. Wait for the download to complete (~4GB, may take a while)
4. Once complete, the instance is ready to start

---

### Starting the Server

1. Click **Start** in the AMP UI
2. The server will load mods and map — allow ~30 seconds
3. Status will show **Running** once ready
4. Players can connect via: `connect <server-ip>:27017` in the NS2 client console

---

### Web Admin

The NS2 built-in web admin is accessible at:

```
http://<server-ip>:<WebAdminPort>/index.html
```

Default port is **8090**. Login with the username and password set in AMP's Configuration settings.

> Note: The web admin port must not conflict with any other AMP instance ports. Check your existing instances before deploying.

---

### Configuration

All settings are managed through AMP's **Configuration** tab:

| Setting | Description |
|---|---|
| Server Name | Name shown in the NS2 server browser |
| Max Players | Maximum players (recommended: 24) |
| Web Admin Port | Port for the browser-based admin UI |
| Web Admin Username | Login username for web admin |
| Web Admin Password | Login password for web admin |

---

### Bots

By default, NS2 may enable filler bots. To disable them, edit `ServerConfig.json` after first launch:

```bash
nano "/home/amp/.ampdata/instances/NS201/.virtualhome/.config/Natural Selection 2/ServerConfig.json"
```

Set the following values:
```json
"auto_vote_add_commander_bots": false,
"filler_bots": 0,
"rookie_only_bots": 0
```

---

### Map Rotation

Map rotation is configured via `MapCycle.json` in the NS2 config directory:

```bash
nano "/home/amp/.ampdata/instances/NS201/.virtualhome/.config/Natural Selection 2/MapCycle.json"
```

---

### Stopping the Server

Click **Stop** in the AMP UI. NS2 uses SIGKILL so it will appear as a crash in the NS2 logs — this is harmless. The server stops cleanly from AMP's perspective.

---

### Updating NS2

1. Click **Support and Updates → Update** in the AMP UI
2. Enter Steam credentials when prompted
3. SteamCMD will verify and update the installation
4. Restart the server after updating

---

### Known Issues

- NS2 logs an `Invalid command line (Unrecognized switch/option name 'selection')` error on every start — this is harmless and does not affect server functionality
- The `-lan` flag may log a warning — also harmless, server operates correctly in LAN mode
- Stop shows as a crash in NS2 logs due to SIGKILL — data is not lost

---

### LAN Party Notes

- Server runs in LAN-only mode (`-lan` flag) — not visible on public server browser
- For future WAN access via Pangolin, remove the `-lan` flag from the launch arguments in `GenericModule.kvp`
- Default map is `ns2_summit` — change via MapCycle.json for rotation

---

### Port Reference

| Port | Protocol | Purpose |
|---|---|---|
| 27017 | UDP | Main game traffic |
| 27018 | UDP | Steam query |
| 8090 | TCP | Web admin UI |


---

# Spellbreak Community (Linux Wine)

Template: `spellbreak-wine.kvp`. Runs the Windows-only Spellbreak Community Version server under Wine in the `cubecoders/ampbase:wine-stable` container.

## How it runs

AMP starts `spellbreak-run.sh` (fetched into the server folder on Update). The launcher starts `g3Server-Win64-Test.exe` under Wine, follows the game's own log (`g3/Saved/Logs/g3-<pid>.log`) and pipes it to AMP's console with the `!json{...}` metadata stripped, and stops the game cleanly when AMP stops the instance. AMP tracks the real game process for CPU and memory.

## Setup

1. Create the instance on AMPHost01 from **Spellbreak Community (Linux Wine)**. Set After Creation to **Do Nothing**.
2. Copy the Windows server folder (the one with `g3`, `Engine`, `Prereqs` and the `.bat` files) into `/home/amp/.ampdata/instances/<Instance>/spellbreak/server/` and `chown -R amp:amp` it. Or set **Server Package URL** to a zip of that folder.
3. Click **Update** (fetches the launcher, builds the Wine prefix, installs the UE4 prereqs), then **Start**.

The optional "Download Server Package" step shows an error when Server Package URL is blank. That is expected; the update continues.

## Settings

| Setting | Notes |
|---|---|
| Game Mode | Solo, Duo, Squad, Dominion (Dominion launches `game=Capture`). Restart after changing. |
| Map | Defaults to `Alpha_Resculpt` |

## Ports

| Port | Protocol | Purpose |
|---|---|---|
| 7777 | UDP | Game traffic. Players join with `<server-ip>:7777` in the client. |

## Console

AMP's console is read only in practice: the Spellbreak server does not accept typed commands. Player joins are detected from the `Recieved join options,{ PlayerName => name` log line (the misspelling is the game's).

## Web UI

`spellbreak-webui/` is a small Python (stdlib only) site: a public join page plus a PIN-protected admin area (overview with charts, match setup cards, players and session history, filterable console, settings).

```bash
# on the AMP host, as root
cd spellbreak-webui && bash install.sh
systemctl restart spellbreak-webui
```

Then open `http://<host>:8095` and follow the first-run setup (AMP user, password, admin PIN). Settings and history live in `/var/lib/spellbreak-webui`.

AMP permissions for the web UI user:

- On the controller (192.168.5.13): user-specific permission for the Spellbreak instance (`Instances.<id>.*`).
- Inside the instance: a role (e.g. "Spellbreak WebUI") allowing Start, Stop, Restart, Read Console and `Settings.Meta.GenericModule.*`, with the user as a member.

`AMP_URL` in `/etc/spellbreak-webui.env` must point at the controller that handles logins (`http://192.168.5.13:8080`), not the local target ADS.

## Project Reclaimer (Docker)

Template: `project-reclaimer.kvp`. Runs a Project Reclaimer (Halo 3) dedicated server from the official image `ghcr.io/projectreclaimer/project-reclaimer-dedicated`, one AMP instance per server, the same way the Reclaimer docs' `compose.yaml` runs one container per server.

### How it runs

AMP runs `reclaimer-run.sh` (fetched on Update) on the host, not in an AMP container. The launcher runs `docker run --rm --network host` with the instance's `reclaimer/server` folder mounted as `/server`, stops it with SIGINT on AMP Stop, and pipes its log to AMP's console. The game port AMP assigns is passed as `RECLAIMER_DEDICATED_ONLY`, so the instance runs the `[[server]]` in `dedicated.toml` with that port. Works with Podman if Docker isn't installed.

Requirement: the `amp` user must be able to run `docker` (`sudo usermod -aG docker amp`, then restart ADS). The launcher says so in the console if not.

### Setup

1. Create the instance from **Project Reclaimer (Docker)**, After Creation **Update Once**.
2. Upload the `game` folder made by `project-reclaimer.exe dedicated init C:\reclaimer --from` on a Windows PC with MCC + Halo 3 into `reclaimer/server/game` (AMP SFTP or File Manager).
3. Start. The first start writes a commented `reclaimer/server/dedicated.toml` with two servers (UDP 49176 and 49177). Edit it, keeping the `port` of the instance's `[[server]]` equal to the AMP Game Port, then restart.

Several instances can share one game folder: set **Shared Game Files Folder** to an absolute host path owned by `amp`.

### Console

Typing in AMP's console runs `dedicated <command>` in the container: `status`, `rcon players`, `ban <player ID | IP> --reason "..."`, `check`.

### Ports

| Port | Protocol | Purpose |
| --- | --- | --- |
| 49176 (Game Port) | UDP | Players and the Server Browser. Next instance: 49177, and so on |
| 49175 | TCP | This machine's master (shared by every instance). Optional; open it if LAN players use it. |

### Updating

Click Update: it pulls the image set in **Docker Image** (`:latest` by default). Restart the instance afterwards. Pin a version tag to stay on it.

### Notes

- AMP's CPU and memory graphs show the launcher, not the container. Use `docker stats`.
- Per-instance `RECLAIMER_DEDICATED_*` overrides go in `reclaimer/reclaimer.env`.
