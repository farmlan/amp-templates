#!/bin/bash
# Project Reclaimer AMP launcher.
# Runs ONE Reclaimer dedicated server from the official Docker image, in the
# foreground, so AMP can start it, stop it and show its log. This is the same
# container compose.yaml in the Reclaimer docs runs per server (host network,
# SIGINT stop, /server and /cache mounts), with the server folder in this
# AMP instance: <instance>/reclaimer/server.
#
#   reclaimer-run.sh run  port=49176 image=... gamedir=... cpus=... memory=...
#   reclaimer-run.sh pull image=...
#
# Typing in AMP's console runs "dedicated <what you typed>" inside the running
# container, e.g.  status   |   rcon players   |   ban <id> --reason "..."
set -u

cd "$(dirname "$0")" || exit 1
LAUNCHER_DIR="$PWD"
ROOT_DIR="$(dirname "$LAUNCHER_DIR")"          # <instance>/reclaimer
SERVER_DIR="$ROOT_DIR/server"
INSTANCE="$(basename "$(dirname "$ROOT_DIR")")"  # AMP instance name
NAME="amp-reclaimer-$(echo "$INSTANCE" | tr 'A-Z' 'a-z' | tr -c 'a-z0-9_.\n-' '-')"
DEFAULT_IMAGE="ghcr.io/projectreclaimer/project-reclaimer-dedicated:latest"

MODE="${1:-run}"; shift || true
PORT=""; IMAGE=""; GAMEDIR=""; CPUS=""; MEMORY=""
for a in "$@"; do
  case "$a" in
    port=*)   PORT="${a#port=}" ;;
    image=*)  IMAGE="${a#image=}" ;;
    gamedir=*) GAMEDIR="${a#gamedir=}" ;;
    cpus=*)   CPUS="${a#cpus=}" ;;
    memory=*) MEMORY="${a#memory=}" ;;
  esac
done
[ -z "$IMAGE" ] && IMAGE="$DEFAULT_IMAGE"

if command -v docker >/dev/null 2>&1; then CT=docker
elif command -v podman >/dev/null 2>&1; then CT=podman
else echo "ERROR: neither docker nor podman is installed for user $(id -un) on this host."; exit 1; fi
if ! $CT info >/dev/null 2>&1; then
  echo "ERROR: user $(id -un) can't use $CT. On the host run: sudo usermod -aG docker $(id -un)  then restart ADS (sudo systemctl restart ampinstmgr) or reboot."
  exit 1
fi

if [ "$MODE" = "pull" ]; then
  echo "Pulling $IMAGE with $CT..."
  exec $CT pull "$IMAGE"
fi

if [ -z "$PORT" ]; then echo "ERROR: no game port given"; exit 1; fi

# Folders must exist before the container starts, or Docker creates them owned
# by root. The servers run as the owner of the server folder (amp).
mkdir -p "$SERVER_DIR/game"
ARGS=(run --rm --name "$NAME"
  --network host
  --stop-signal SIGINT --stop-timeout 30
  --security-opt no-new-privileges:true
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=3
  -v "$SERVER_DIR:/server"
  -v "reclaimer-cache:/cache"
  -e "RECLAIMER_DEDICATED_ONLY=$PORT")
if [ -n "$GAMEDIR" ]; then
  if [ ! -d "$GAMEDIR" ]; then echo "ERROR: Shared Game Files Folder $GAMEDIR doesn't exist on the host"; exit 1; fi
  ARGS+=(-v "$GAMEDIR:/server/game:ro")
fi
# Optional per-instance settings, same as compose's .env (RECLAIMER_DEDICATED_*).
[ -f "$ROOT_DIR/reclaimer.env" ] && ARGS+=(--env-file "$ROOT_DIR/reclaimer.env")
[ -n "$CPUS" ] && ARGS+=(--cpus "$CPUS")
[ -n "$MEMORY" ] && ARGS+=(--memory "$MEMORY")
ARGS+=("$IMAGE")

# A container left over from a crash would hold the name and the port.
$CT rm -f "$NAME" >/dev/null 2>&1

STOPPING=0
stop_server() {
  STOPPING=1
  echo "Stopping Project Reclaimer ($NAME)..."
  $CT stop -t 30 "$NAME" >/dev/null 2>&1
}
trap stop_server TERM INT

$CT image inspect "$IMAGE" >/dev/null 2>&1 || $CT pull "$IMAGE"
echo "Starting $NAME from $IMAGE on UDP $PORT (server folder: $SERVER_DIR)"
$CT "${ARGS[@]}" </dev/null 2>&1 &
CPID=$!

# AMP console input -> "dedicated <command>" in the running container.
exec 3<&0
(
  while IFS= read -r line <&3; do
    [ -z "$line" ] && continue
    # sh -c keeps quoted arguments together, e.g. --reason "two words"
    $CT exec "$NAME" sh -c "dedicated $line" 2>&1
  done
) &
RPID=$!

while kill -0 "$CPID" 2>/dev/null; do wait "$CPID"; done
wait "$CPID" 2>/dev/null; RC=$?
kill "$RPID" 2>/dev/null
[ "$STOPPING" = 1 ] && { echo "Project Reclaimer stopped"; exit 0; }
echo "Project Reclaimer container exited ($RC)"
exit "$RC"
