#!/bin/bash
# Installs/updates the Spellbreak web UI on this host (run as root).
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
install -d /opt/spellbreak-webui
install -m 644 "$SRC/app.py" "$SRC/index.html" /opt/spellbreak-webui/
install -m 644 "$SRC/spellbreak-webui.service" /etc/systemd/system/
[ -f /etc/spellbreak-webui.env ] || install -m 600 "$SRC/spellbreak-webui.env.example" /etc/spellbreak-webui.env
systemctl daemon-reload
systemctl enable spellbreak-webui >/dev/null 2>&1
systemctl restart spellbreak-webui
echo "Installed. Edit /etc/spellbreak-webui.env then: systemctl restart spellbreak-webui"
