FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    xvfb \
    x11vnc \
    fluxbox \
    novnc \
    websockify \
    netcat-openbsd \
    x11-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libfontconfig1 \
    libice6 \
    libasound2 \
    libpulse0 \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Copy and fix startup script (ensure Unix line endings)
COPY start.sh /app/start.sh
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh

# Pygame needs SDL to not try to open a real display
ENV SDL_VIDEODRIVER=x11
ENV SDL_AUDIODRIVER=dummy
ENV DISPLAY=:99

# Expose websockify port (Render will map $PORT)
EXPOSE 10000

CMD ["/app/start.sh"]
