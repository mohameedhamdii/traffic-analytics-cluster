# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Edge Node Dockerfile — Raspberry Pi 5 + Hailo-8L
#
# Base image: Raspberry Pi OS 64-bit (Bookworm) compatible ARM64 image.
# The hailo_platform package requires the Hailo PCIe driver installed on the
# host; it cannot be bundled in the container.
#
# Build: docker build -t traffic-edge-node .
# Run:   docker run --privileged \
#          --device=/dev/video0:/dev/video0 \
#          --device=/dev/hailo0:/dev/hailo0 \
#          -e NODE_ID=node-1 \
#          -e MQTT_BROKER_HOST=192.168.1.100 \
#          traffic-edge-node
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bookworm AS base

# Avoid interactive prompts during package install
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# ── System dependencies ───────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    # OpenCV system libs
    libglib2.0-0 \
    libgl1-mesa-glx \
    libgstreamer1.0-0 \
    libgstreamer-plugins-base1.0-0 \
    libgtk-3-0 \
    libavcodec-dev \
    libavformat-dev \
    libswscale-dev \
    # Build tools (for scipy wheels)
    gcc \
    g++ \
    # Camera (for picamera2 on Pi)
    libcamera0 \
    libcamera-apps \
    python3-picamera2 \
    # Utilities
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY . /app/

# ── Models directory — populated at runtime via K3s volume mount ─────────────
RUN mkdir -p /app/models

# ── Non-root user for security ────────────────────────────────────────────────
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# ── Runtime ───────────────────────────────────────────────────────────────────
CMD ["python", "main.py"]
