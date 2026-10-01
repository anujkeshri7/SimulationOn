#!/bin/bash
set -e

echo "=== Starting PGK Artillery Simulation ==="

# Start virtual display
echo "[1/5] Starting Xvfb virtual display..."
Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
export DISPLAY=:99

# Wait until Xvfb is truly ready
sleep 3
echo "[1/5] Xvfb started (PID=$XVFB_PID)"

# Start window manager
echo "[2/5] Starting fluxbox window manager..."
fluxbox &
sleep 2

# Start x11vnc - wait up to 20s for DISPLAY to be ready
echo "[3/5] Starting x11vnc VNC server..."
x11vnc \
    -display :99 \
    -forever \
    -shared \
    -nopw \
    -listen 127.0.0.1 \
    -xkb \
    -noxdamage \
    -quiet \
    -bg
echo "[3/5] x11vnc started"

# Wait for VNC port 5900 to be ready (poll every 0.5s, up to 30s)
echo "[4/5] Waiting for VNC server port 5900..."
MAX_WAIT=60
COUNT=0
until nc -z 127.0.0.1 5900 2>/dev/null; do
    if [ $COUNT -ge $MAX_WAIT ]; then
        echo "ERROR: VNC server did not start in time!" >&2
        exit 1
    fi
    sleep 0.5
    COUNT=$((COUNT+1))
done
echo "[4/5] VNC port 5900 is ready after ${COUNT} polls."

# Start pygame simulation in background
echo "[5/5] Starting Pygame simulation..."
python /app/main.py &
SIM_PID=$!
echo "[5/5] Simulation started (PID=$SIM_PID)"

# Find noVNC web root
NOVNC_WEB=""
for p in /usr/share/novnc /usr/local/novnc /opt/novnc /usr/share/novnc/utils; do
    if [ -f "${p}/vnc.html" ]; then
        NOVNC_WEB="$p"
        break
    fi
done

if [ -z "$NOVNC_WEB" ]; then
    echo "noVNC web root not found! Searching..."
    NOVNC_WEB=$(find / -name "vnc.html" 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
    echo "Found: $NOVNC_WEB"
fi

# Port from Render env
PORT="${PORT:-10000}"
echo "=== Starting websockify on port $PORT, VNC=127.0.0.1:5900 ==="
echo "=== Open: http://your-app.onrender.com/vnc.html ==="

exec websockify \
    --web="${NOVNC_WEB}" \
    --heartbeat=30 \
    "${PORT}" \
    127.0.0.1:5900
