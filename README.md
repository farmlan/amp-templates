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
