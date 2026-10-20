#!/bin/bash
# Project Reclaimer AMP launcher, for the cubecoders/ampbase:wine-stable
# container AMP creates for this instance. It does what the official Reclaimer
# Docker image does (Wine + Xvfb + the dedicated build), inside AMP's own
# container, so AMP keeps full control (start/stop/update/console/metrics).
#
#   reclaimer-run.sh update [version=latest]   download the dedicated build, prepare Wine
#   reclaimer-run.sh run port=49176            run the [[server]] with that game port
#
# The server folder is this script's folder (<instance>/reclaimer/server), laid
# out like C:\reclaimer from "dedicated init": dedicated.toml, game/, content/,
# playlists/, bans.json, data/.
set -u
cd "$(dirname "$0")" || exit 1
SERVER_DIR="$PWD"
ROOT_DIR="$(dirname "$SERVER_DIR")"
EXE="project-reclaimer.exe"
REPO="https://github.com/ProjectReclaimer/project-reclaimer-releases"
export WINEPREFIX="${WINEPREFIX:-$ROOT_DIR/.wine}" WINEARCH=win64 WINEDEBUG="${WINEDEBUG:--all}"

MODE="${1:-run}"; shift || true
PORT=""; VERSION=""
for a in "$@"; do
  case "$a" in
    port=*) PORT="${a#port=}" ;;
    version=*) VERSION="${a#version=}" ;;
  esac
done

if [ "$MODE" = "update" ]; then
  set -e
  [ -z "$VERSION" ] || [ "$VERSION" = "latest" ] && VERSION=""
  if [ -z "$VERSION" ]; then
    # /releases/latest/download/<x> redirects to /releases/download/v<version>/<x>
    LOC=$(wget -q -S --max-redirect=0 "$REPO/releases/latest/download/x" -O /dev/null 2>&1 | sed -n 's/^ *[Ll]ocation: *//p' | head -1)
    VERSION=$(echo "$LOC" | sed -n 's#.*/download/v\([^/]*\)/.*#\1#p')
    [ -n "$VERSION" ] || { echo "Couldn't find the latest Project Reclaimer release"; exit 1; }
  fi
  VERSION="${VERSION#v}"
  CUR=$(cat .reclaimer-version 2>/dev/null || true)
  if [ "$CUR" = "$VERSION" ] && [ -f "$EXE" ]; then
    echo "Project Reclaimer $VERSION is already installed"
  else
    echo "Downloading Project Reclaimer $VERSION (dedicated build)..."
    BASE="$REPO/releases/download/v$VERSION"
    ASSET="project-reclaimer-dedicated-v$VERSION.exe"
    if ! wget -q "$BASE/$ASSET" -O "$EXE.new"; then
      ASSET="project-reclaimer-dedicated-$VERSION.exe"   # older naming
      wget -q "$BASE/$ASSET" -O "$EXE.new" || { rm -f "$EXE.new"; echo "Release v$VERSION has no dedicated build"; exit 1; }
    fi
    # Check it against the release's SHA256SUMS.txt when the release has one.
    if wget -q "$BASE/SHA256SUMS.txt" -O .sums 2>/dev/null; then
      WANT=$(grep -i " \*\?$ASSET\$" .sums | awk '{print tolower($1)}')
      GOT=$(sha256sum "$EXE.new" | awk '{print $1}')
      rm -f .sums
      if [ -n "$WANT" ] && [ "$WANT" != "$GOT" ]; then rm -f "$EXE.new"; echo "Checksum mismatch for $ASSET"; exit 1; fi
      [ -n "$WANT" ] && echo "Checksum OK"
    fi
    mv -f "$EXE.new" "$EXE"
    echo "$VERSION" > .reclaimer-version
    echo "Installed Project Reclaimer $VERSION"
  fi
  # A prefix left half-built by an interrupted update can't load kernel32.dll:
  # check it really works, and rebuild it if not.
  prefix_ok() { [ "$(timeout 120 wine cmd.exe /c echo ok 2>/dev/null | tr -d '\r')" = "ok" ]; }
  if [ -f "$WINEPREFIX/.prefix-ready" ] && ! prefix_ok; then
    echo "The Wine prefix is damaged: rebuilding it"
    timeout 30 wineserver -k 2>/dev/null || true
    rm -rf "$WINEPREFIX"
  fi
  if [ ! -f "$WINEPREFIX/.prefix-ready" ]; then
    echo "Creating the Wine prefix..."
    timeout 30 wineserver -k 2>/dev/null || true
    rm -rf "$WINEPREFIX"
    # Skip the Mono/Gecko install prompts, which would wait forever on Xvfb.
    WINEDLLOVERRIDES="mscoree=;mshtml=" timeout 300 wineboot --init >/dev/null 2>&1 || true
    timeout 120 wineserver -w 2>/dev/null || true
    if prefix_ok; then
      touch "$WINEPREFIX/.prefix-ready"
      echo "Wine prefix ready ($(wine --version 2>/dev/null))"
    else
      echo "ERROR: Wine can't run programs in this container (wine cmd.exe failed)."
      timeout 60 wine cmd.exe /c echo ok 2>&1 | tail -n 10 | sed 's/^/  wine: /'
      exit 1
    fi
  fi
  if [ ! -f "$WINEPREFIX/.vcrun2022" ]; then
    # Same as the Reclaimer docs' Linux setup: winetricks vcrun2022. Not fatal:
    # if it can't finish, Wine's own built-in runtime DLLs are used instead.
    echo "Installing the Visual C++ 2015-2022 runtime into Wine (winetricks vcrun2022, a few minutes)..."
    if wget -q "https://raw.githubusercontent.com/Winetricks/winetricks/refs/tags/20260125/src/winetricks" -O "$ROOT_DIR/winetricks"; then
      chmod +x "$ROOT_DIR/winetricks"
      if timeout 900 xvfb-run -a "$ROOT_DIR/winetricks" -q --unattended vcrun2022 > "$ROOT_DIR/winetricks.log" 2>&1; then
        touch "$WINEPREFIX/.vcrun2022"
        echo "Visual C++ runtime installed"
      else
        echo "WARNING: winetricks vcrun2022 didn't finish. The server will use Wine's built-in runtime; Update tries again next time. Last lines of its log:"
        grep -v -E '^(d|-|l)[rwx-]{9} |^total [0-9]+$' "$ROOT_DIR/winetricks.log" | grep -v '^$' | tail -n 40 | sed 's/^/  winetricks: /'
      fi
      timeout 30 wineserver -k 2>/dev/null || true
    else
      echo "WARNING: couldn't download winetricks; Update will try again next time"
    fi
  fi
  mkdir -p game content playlists data
  echo "Update finished"
  exit 0
fi

# ---- run ----
[ -n "$PORT" ] || { echo "ERROR: no game port given"; exit 1; }
[ -f "$EXE" ] || { echo "ERROR: $EXE is missing: click Update first"; exit 1; }
if [ ! -f "game/halo3/halo3.dll" ] && [ ! -f "game/halo3/maps/shared.map" ]; then
  echo "WARNING: no Halo 3 files in reclaimer/server/game yet. Upload them (see the README); the server will say what's missing."
fi
export RECLAIMER_DEDICATED_ONLY="$PORT"
# Optional per-instance overrides, as in the Docker .env (RECLAIMER_DEDICATED_*=...)
if [ -f "$ROOT_DIR/reclaimer.env" ]; then set -a; . "$ROOT_DIR/reclaimer.env"; set +a; fi

GAMEPID=""
STOPPING=0
stop_server() {
  STOPPING=1
  echo "Stopping Project Reclaimer..."
  # Ctrl+C is the documented clean stop; Wine passes SIGINT on as Ctrl+C.
  # Signal the supervisor (the wine process), not xvfb-run.
  pkill -INT -f "project-reclaimer\.exe dedicated *\$" 2>/dev/null
  for _ in $(seq 1 30); do kill -0 "$GAMEPID" 2>/dev/null || break; sleep 1; done
  if kill -0 "$GAMEPID" 2>/dev/null; then
    echo "Still running after 30 s, forcing it to close"
    wineserver -k 2>/dev/null; kill -KILL "$GAMEPID" 2>/dev/null
  fi
}
trap stop_server TERM INT

echo "Starting Project Reclaimer $(cat .reclaimer-version 2>/dev/null) on UDP $PORT"
xvfb-run -a wine "$EXE" dedicated </dev/null 2>&1 &
GAMEPID=$!

# AMP console: each line runs "dedicated <command>" against the running server,
# e.g.  status   |   ban <player ID | IP> --reason "..."   |   rcon players
exec 3<&0
(
  while IFS= read -r line <&3; do
    [ -z "$line" ] && continue
    eval "set -- $line"
    wine "$EXE" dedicated "$@" </dev/null 2>&1 | sed -u 's/\r$//'
  done
) &
RPID=$!

while [ -n "$GAMEPID" ] && kill -0 "$GAMEPID" 2>/dev/null; do wait "$GAMEPID"; done
kill "$RPID" 2>/dev/null
[ "$STOPPING" = 1 ] && { echo "Project Reclaimer stopped"; exit 0; }
echo "Project Reclaimer exited"
exit 1
