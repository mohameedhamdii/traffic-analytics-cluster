#!/usr/bin/env bash
# =============================================================================
# deploy-all.sh — Build images and deploy all workloads to the K3s cluster
#
# Run on the CENTRAL SERVER (with kubectl access to the cluster):
#   chmod +x deploy-all.sh && ./deploy-all.sh
#
# Options:
#   --build-only   — build Docker images but don't deploy
#   --skip-build   — deploy existing images without building
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_ONLY=false
SKIP_BUILD=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-only) BUILD_ONLY=true; shift ;;
    --skip-build) SKIP_BUILD=true; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo "======================================================================"
echo "  Smart City Traffic Analytics — Full Cluster Deploy"
echo "  Repo: ${REPO_ROOT}"
echo "======================================================================"

# ── 1. Build Docker images ───────────────────────────────────────────────────
if [[ "$SKIP_BUILD" == false ]]; then
  echo ""
  echo "[1/4] Building edge-node Docker image..."
  docker build -t traffic-edge-node:latest "${REPO_ROOT}/edge-node/"

  echo ""
  echo "[1/4] Building aggregator Docker image..."
  docker build -t traffic-aggregator:latest "${REPO_ROOT}/central-server/"

  echo "  Images built ✓"
fi

if [[ "$BUILD_ONLY" == true ]]; then
  echo "  --build-only specified. Stopping after build."
  exit 0
fi

# ── 2. Push / export images to cluster nodes ─────────────────────────────────
echo ""
echo "[2/4] Exporting images for k3s import..."
echo "  (If using a private registry, push there and update image: fields in YAML)"
echo ""
echo "  Saving traffic-edge-node:latest → /tmp/edge-node.tar"
docker save traffic-edge-node:latest -o /tmp/edge-node.tar

echo "  Saving traffic-aggregator:latest → /tmp/aggregator.tar"
docker save traffic-aggregator:latest -o /tmp/aggregator.tar

echo ""
echo "  Import on each edge node with:"
echo "    scp /tmp/edge-node.tar pi@node-1:/tmp/"
echo "    ssh pi@node-1 'sudo k3s ctr images import /tmp/edge-node.tar'"
echo ""
echo "  Import aggregator on master:"
echo "    sudo k3s ctr images import /tmp/aggregator.tar"

# ── 3. Deploy K3s manifests ───────────────────────────────────────────────────
echo ""
echo "[3/4] Deploying K3s manifests..."
kubectl apply -f "${REPO_ROOT}/k3s/namespace.yaml"
kubectl apply -f "${REPO_ROOT}/k3s/central-services.yaml"
kubectl apply -f "${REPO_ROOT}/k3s/edge-node-deployment.yaml"

echo "  Manifests applied ✓"

# ── 4. Start central services via docker-compose ─────────────────────────────
echo ""
echo "[4/4] Starting central services (Mosquitto, InfluxDB, Grafana)..."
cd "${REPO_ROOT}"

if [[ ! -f ".env" ]]; then
  echo "  ⚠️  No .env file found — copying from .env.example"
  cp .env.example .env
  echo "  ✏️  Please edit .env and set INFLUXDB_TOKEN before deploying for real."
fi

docker compose up -d --remove-orphans

echo ""
echo "======================================================================"
echo "✅ Deployment complete!"
echo ""
echo "  Grafana dashboard: http://localhost:3000"
echo "  InfluxDB UI:       http://localhost:8086"
echo "  MQTT broker:       localhost:1883"
echo ""
echo "  Check K3s pods:    kubectl get pods -n traffic-analytics -w"
echo "======================================================================"
