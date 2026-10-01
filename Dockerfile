FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    net-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN echo '#!/bin/bash\n\
Xvfb :99 -screen 0 1366x768x24 &\n\
export DISPLAY=:99\n\
sleep 2\n\
fluxbox &\n\
x11vnc -display :99 -forever -shared -nopw -quiet -listen 127.0.0.1 &\n\
sleep 2\n\
python main.py &\n\
PORT=${PORT:-10000}\n\
echo "Starting websockify on port $PORT..."\n\
websockify --web=/usr/share/novnc/ $PORT 127.0.0.1:5900\n\
' > /app/start.sh

RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
