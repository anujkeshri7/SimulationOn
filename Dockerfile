FROM python:3.11-slim

# Install necessary packages for virtual display and VNC
RUN apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create startup script
RUN echo '#!/bin/bash\n\
Xvfb :99 -screen 0 1366x768x24 &\n\
export DISPLAY=:99\n\
fluxbox &\n\
x11vnc -display :99 -forever -nopw -quiet -listen localhost -xkb &\n\
python main.py &\n\
PORT=${PORT:-10000}\n\
websockify --web=/usr/share/novnc/ $PORT localhost:5900\n\
' > /app/start.sh

RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
