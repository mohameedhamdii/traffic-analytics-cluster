# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────────
# Central Server — Aggregator Dockerfile
#
# Lightweight Python 3.11 image; no camera or Hailo dependencies needed.
# Build: docker build -t traffic-aggregator .
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

WORKDIR /app

# ── Python dependencies ───────────────────────────────────────────────────────
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ── Application source ────────────────────────────────────────────────────────
COPY . /app/

# ── Non-root user ─────────────────────────────────────────────────────────────
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# ── Runtime ───────────────────────────────────────────────────────────────────
CMD ["python", "aggregator.py"]
