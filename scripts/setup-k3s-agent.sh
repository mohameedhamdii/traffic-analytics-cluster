#!/usr/bin/env bash
# =============================================================================
# setup-k3s-agent.sh — Install K3s agent on an edge Raspberry Pi 5 node
#
# Usage (run on each edge Pi):
#   chmod +x setup-k3s-agent.sh
#   sudo ./setup-k3s-agent.sh \
#     --master-ip 192.168.1.100 \
#     --token <node-token-from-master> \
#     --node-name node-1
# =============================================================================

set -euo pipefail

# ── Parse arguments ─────────────────────────────────────────────────────────
MASTER_IP=""
TOKEN=""
NODE_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --master-ip) MASTER_IP="$2"; shift 2 ;;
    --token)     TOKEN="$2"; shift 2 ;;
    --node-name) NODE_NAME="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

if [[ -z "$MASTER_IP" || -z "$TOKEN" || -z "$NODE_NAME" ]]; then
  echo "Usage: $0 --master-ip <IP> --token <TOKEN> --node-name <NAME>"
  exit 1
fi

echo "======================================================================"
echo "  Installing K3s agent: ${NODE_NAME} → master: ${MASTER_IP}"
echo "======================================================================"

# ── 1. Set hostname ─────────────────────────────────────────────────────────
echo "[1/6] Setting hostname to ${NODE_NAME}..."
hostnamectl set-hostname "${NODE_NAME}"

# ── 2. Enable cgroup memory (required for K3s on Raspberry Pi) ──────────────
echo "[2/6] Enabling cgroup memory..."
CMDLINE="/boot/firmware/cmdline.txt"
if [[ -f "$CMDLINE" ]] && ! grep -q "cgroup_memory=1" "$CMDLINE"; then
  sed -i 's/$/ cgroup_memory=1 cgroup_enable=memory/' "$CMDLINE"
  echo "Added cgroup params to cmdline.txt — a reboot is required."
fi

# ── 3. Install Hailo PCIe driver (if not already installed) ─────────────────
echo "[3/6] Checking Hailo PCIe driver..."
if ! lsmod | grep -q hailo; then
  echo "  Hailo driver not loaded — install it manually from:"
  echo "  https://hailo.ai/developer-zone/documentation/"
  echo "  Continuing without Hailo driver (ONNX fallback will be used)."
else
  echo "  Hailo driver loaded ✓"
fi

# ── 4. Install K3s agent ─────────────────────────────────────────────────────
echo "[4/6] Installing K3s agent..."
curl -sfL https://get.k3s.io | \
  K3S_URL="https://${MASTER_IP}:6443" \
  K3S_TOKEN="${TOKEN}" \
  K3S_NODE_NAME="${NODE_NAME}" \
  sh -

echo "Waiting for K3s agent to connect..."
sleep 10

# ── 5. Verify the node is visible on the master ──────────────────────────────
echo "[5/6] Node registered. Verify on master with: kubectl get nodes"

# ── 6. Create model and config directories ───────────────────────────────────
echo "[6/6] Creating host directories for models and config..."
mkdir -p /opt/traffic-models
mkdir -p /opt/traffic-config

echo ""
echo "✅ K3s agent setup complete on ${NODE_NAME}!"
echo ""
echo "Next steps (run on master):"
echo "  kubectl label node ${NODE_NAME} role=edge-camera"
echo "  kubectl apply -f k3s/edge-node-deployment.yaml"
echo ""
echo "Then copy models to this node:"
echo "  scp models/yolov8n-traffic.hef pi@${NODE_NAME}:/opt/traffic-models/"
echo "  scp -r edge-node/config/* pi@${NODE_NAME}:/opt/traffic-config/"
