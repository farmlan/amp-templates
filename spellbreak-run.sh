#!/bin/bash
# Spellbreak AMP launcher: runs the Windows server under Wine and mirrors its
# log file to stdout, because Wine does not pass the game's stdout to AMP reliably.
cd "$(dirname "$0")" || exit 1
EXE="g3/Binaries/Win64/g3Server-Win64-Test.exe"
LOGDIR="g3/Saved/Logs"
mkdir -p "$LOGDIR"
STAMP=$(mktemp)

stop_server() {
    echo "Stopping Spellbreak server..."
    pkill -INT -f 'g3Server-Win64-Test\.exe' 2>/dev/null
    for _ in $(seq 1 20); do pgrep -f 'g3Server-Win64-Test\.exe' >/dev/null || break; sleep 1; done
    wineserver -k 2>/dev/null
    rm -f "$STAMP"
    exit 0
}
trap stop_server TERM INT

wine "$EXE" "$@" >/dev/null 2>&1 &

# Wait for the new log file (UE names it g3-<pid>.log on each run)
LOG=""
for _ in $(seq 1 90); do
    LOG=$(find "$LOGDIR" -maxdepth 1 -name 'g3*.log' -newer "$STAMP" -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)
    [ -n "$LOG" ] && break
    sleep 1
done
if [ -z "$LOG" ]; then echo "No server log appeared, check Wine"; stop_server; fi
echo "Following $LOG"

# Strip the trailing ' !json{...}' metadata so AMP regexes stay simple
tail -n +1 -F "$LOG" 2>/dev/null | sed -u -e 's/\r$//' -e 's/ !json{.*}$//' &
TAILPID=$!

sleep 5
while pgrep -f 'g3Server-Win64-Test\.exe' >/dev/null; do sleep 2; done
kill "$TAILPID" 2>/dev/null
rm -f "$STAMP"
echo "Spellbreak server exited"
