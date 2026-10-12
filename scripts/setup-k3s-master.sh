#!/usr/bin/env bash
# =============================================================================
# setup-k3s-master.sh — Install K3s master node on the central server
#
# Run on the CENTRAL SERVER (not on edge nodes):
#   chmod +x setup-k3s-master.sh && sudo ./setup-k3s-master.sh
# =============================================================================

set -euo pipefail

CLUSTER_CIDR="10.42.0.0/16"
SERVICE_CIDR="10.43.0.0/16"
NODE_IP=$(hostname -I | awk '{print $1}')

echo "======================================================================"
echo "  Installing K3s master on: ${NODE_IP}"
echo "======================================================================"

# ── 1. Install K3s ─────────────────────────────────────────────────────────
echo "[1/5] Installing K3s server..."
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="\
  server \
  --cluster-cidr=${CLUSTER_CIDR} \
  --service-cidr=${SERVICE_CIDR} \
  --node-ip=${NODE_IP} \
  --write-kubeconfig-mode=644 \
  --disable=traefik \
  --disable=servicelb" \
sh -

echo "Waiting for K3s to start..."
sleep 15

# ── 2. Configure kubectl ─────────────────────────────────────────────────────
echo "[2/5] Configuring kubectl..."
mkdir -p "$HOME/.kube"
sudo cp /etc/rancher/k3s/k3s.yaml "$HOME/.kube/config"
sudo chown "$USER:$USER" "$HOME/.kube/config"
export KUBECONFIG="$HOME/.kube/config"

kubectl get nodes

# ── 3. Save join token ───────────────────────────────────────────────────────
echo "[3/5] Retrieving node join token..."
TOKEN=$(sudo cat /var/lib/rancher/k3s/server/node-token)
echo ""
echo "======================================================================"
echo "  NODE JOIN TOKEN (save this for edge node setup):"
echo "  ${TOKEN}"
echo "======================================================================"
echo ""
echo "  Master IP: ${NODE_IP}"
echo "  Join command for edge nodes:"
echo "  curl -sfL https://get.k3s.io | K3S_URL=https://${NODE_IP}:6443 K3S_TOKEN=${TOKEN} sh -"
echo "======================================================================"

# ── 4. Create namespace ──────────────────────────────────────────────────────
echo "[4/5] Creating traffic-analytics namespace..."
kubectl apply -f "$(dirname "$0")/../k3s/namespace.yaml"

# ── 5. Label master node ─────────────────────────────────────────────────────
echo "[5/5] Labelling master node..."
MASTER_NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl label node "${MASTER_NODE}" node-role.kubernetes.io/master=true --overwrite

echo ""
echo "✅ K3s master setup complete!"
echo "   Next: run setup-k3s-agent.sh on each edge Raspberry Pi."
