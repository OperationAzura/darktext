#!/usr/bin/env bash
# Start the game and diagnostic reader together, with no OCR daemon.
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
INSTALL=${DARKLANDS_INSTALL_ROOT:-"$ROOT/../darklands-accessibility/local"}
DOSBOX=${DARKLANDS_DOSBOX_BIN:-"$INSTALL/bin/dosbox-staging"}
GAME=${DARKLANDS_GAME_EXE:-"$HOME/dosGames/darklands/darkland.exe"}
PYTHON=${DARKTEXT_PYTHON:-python3}
SESSION=${1:-"$ROOT/../ram-recordings/$(date +%Y%m%d-%H%M%S)"}
if [[ ! -x "$DOSBOX" || ! -f "$GAME" ]]; then
    echo 'Set DARKLANDS_DOSBOX_BIN and DARKLANDS_GAME_EXE to the installed emulator and game.' >&2
    exit 1
fi
if [[ -e "$SESSION" ]]; then
    echo "Session directory already exists: $SESSION" >&2
    exit 1
fi
# Avoid accidentally recording another emulator, or launching a second one on its port.
"$PYTHON" - <<'PY'
import socket
with socket.socket() as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(('127.0.0.1', 8086))
    except OSError:
        raise SystemExit('Port 8086 is in use. Close the existing launcher or attach with --record instead.')
PY
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
emulator_pid=''
reader_pid=''
cleanup() {
    trap - EXIT INT TERM
    if [[ -n "$reader_pid" ]]; then
        kill -TERM "$reader_pid" 2>/dev/null || true
        wait "$reader_pid" 2>/dev/null || true
    fi
    if [[ -n "$emulator_pid" ]]; then
        kill -TERM "$emulator_pid" 2>/dev/null || true
        wait "$emulator_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Recorder creates the session exclusively before DOSBox is launched.
"$PYTHON" -m darktext.ram_text --exe "$GAME" --record "$SESSION" &
reader_pid=$!
for ((attempt=0; attempt<50; attempt++)); do
    [[ -f "$SESSION/events.jsonl" ]] && break
    kill -0 "$reader_pid" 2>/dev/null || { wait "$reader_pid"; exit 1; }
    sleep 0.1
done
[[ -f "$SESSION/events.jsonl" ]] || { echo 'Recorder did not start.' >&2; exit 1; }
echo "Recording to: $SESSION"
echo 'Play normally. Close DOSBox or press Ctrl+C here to finish. OCR and automatic speech are off.'
"$DOSBOX" --set core=normal --set webserver_enabled=on \
    --set webserver_bind_address=127.0.0.1 --set webserver_port=8086 \
    "$GAME" > "$SESSION/dosbox.log" 2>&1 &
emulator_pid=$!
# End when either process exits; EXIT trap cleans up only our own children.
wait -n "$emulator_pid" "$reader_pid"
