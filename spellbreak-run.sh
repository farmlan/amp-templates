#!/bin/bash
# Spellbreak AMP launcher: runs the Windows server under Wine and mirrors its
# log file to stdout, because Wine does not pass the game's stdout to AMP reliably.
# Only ever signals the game process it started itself, so a restart can't
# kill the next server.
cd "$(dirname "$0")" || exit 1
EXE="g3/Binaries/Win64/g3Server-Win64-Test.exe"
LOGDIR="g3/Saved/Logs"
mkdir -p "$LOGDIR"
GAMEPID=""
TAILPID=""

game_pids() { pgrep -f 'g3Server-Win64-Test\.exe' 2>/dev/null; }
# The real game process has a Windows path (Z:\...\Win64\g3Server...); wine's
# start.exe helper has the unix path, so match the backslash form.
real_game_pid() { pgrep -n -f 'Win64\\g3Server-Win64-Test\.exe' 2>/dev/null; }

cleanup() {
    [ -n "$TAILPID" ] && pkill -P "$TAILPID" 2>/dev/null
    [ -n "$TAILPID" ] && kill "$TAILPID" 2>/dev/null
    pkill -f "tail -n \+1 -F $LOGDIR/" 2>/dev/null
    rm -f "$STAMP"
}

stop_server() {
    echo "Stopping Spellbreak server..."
    if [ -n "$GAMEPID" ] && kill -0 "$GAMEPID" 2>/dev/null; then
        kill -INT "$GAMEPID" 2>/dev/null
        for _ in $(seq 1 15); do kill -0 "$GAMEPID" 2>/dev/null || break; sleep 1; done
        kill -KILL "$GAMEPID" 2>/dev/null
    fi
    cleanup
    echo "Spellbreak server stopped"
    exit 0
}
trap stop_server TERM INT

# A previous server that is still shutting down would hold port 7777: wait for it.
for _ in $(seq 1 20); do [ -z "$(game_pids)" ] && break; echo "Waiting for previous server to exit..."; sleep 1; done
for p in $(game_pids); do kill -KILL "$p" 2>/dev/null; done
pkill -f "tail -n \+1 -F $LOGDIR/" 2>/dev/null

STAMP=$(mktemp)
wine "$EXE" "$@" >/dev/null 2>&1 &

# Find the game process we just launched
for _ in $(seq 1 60); do GAMEPID=$(real_game_pid); [ -n "$GAMEPID" ] && break; sleep 1; done
[ -z "$GAMEPID" ] && { echo "Server process did not start, check Wine"; cleanup; exit 1; }
echo "Server process $GAMEPID started"

# Wait for its new log file (UE names it g3-<pid>.log on each run)
LOG=""
for _ in $(seq 1 90); do
    LOG=$(find "$LOGDIR" -maxdepth 1 -name 'g3*.log' -newer "$STAMP" -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)
    [ -n "$LOG" ] && break
    kill -0 "$GAMEPID" 2>/dev/null || break
    sleep 1
done
if [ -n "$LOG" ]; then
    echo "Following $LOG"
    # Strip CRs and the trailing ' !json{...}' metadata so AMP regexes stay simple
    tail -n +1 -F "$LOG" 2>/dev/null | sed -u -e 's/\r$//' -e 's/ !json{.*}$//' &
    TAILPID=$!
fi

while kill -0 "$GAMEPID" 2>/dev/null; do sleep 2 & wait $!; done
sleep 1
cleanup
echo "Spellbreak server exited"
