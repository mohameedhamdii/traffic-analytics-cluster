# Cluster Setup Guide

This guide walks through setting up the full Raspberry Pi 5 + Hailo-8L K3s cluster
from scratch, including hardware wiring, OS configuration, K3s installation, and
deploying all workloads.

---

## Hardware You Need

| Item | Qty |
|---|---|
| Raspberry Pi 5 (8 GB) | 4 (3 edge + 1 central) |
| Hailo-8L M.2 AI HAT | 3 (one per edge Pi) |
| Raspberry Pi Camera Module 3 (wide) | 3 |
| 64 GB microSD A2 U3 | 4 |
| 5 V / 5 A USB-C PSU | 4 |
| 5-port Gigabit Ethernet switch | 1 |
| Cat6 patch cables (0.5 m) | 5 |

---

## Step 0 — Flash OS on All Pis

Use **Raspberry Pi Imager** to flash **Raspberry Pi OS 64-bit (Bookworm) Lite** on all 4 microSD cards.

In Imager's advanced options, set:
- Hostname: `central`, `node-1`, `node-2`, `node-3`
- Username/password (e.g. `pi` / `your-password`)
- SSH: **Enable**
- WiFi: optional (wired cluster recommended)

---

## Step 1 — Network Setup

Connect all Pis to the gigabit switch with Ethernet cables.

Assign **static IP addresses** via your router's DHCP reservation, or configure
`/etc/dhcpcd.conf` on each Pi:

```
interface eth0
static ip_address=192.168.1.100/24   # central server
static routers=192.168.1.1
static domain_name_servers=8.8.8.8
```

| Host | Static IP |
|---|---|
| central | 192.168.1.100 |
| node-1 | 192.168.1.101 |
| node-2 | 192.168.1.102 |
| node-3 | 192.168.1.103 |

Update `/etc/hosts` on every Pi to include all hostnames.

---

## Step 2 — NTP Sync (Critical for Timestamps)

On ALL nodes, ensure NTP is configured for accurate inter-node timestamps:

```bash
sudo apt update && sudo apt install -y chrony
sudo timedatectl set-ntp true
chronyc tracking
```

---

## Step 3 — Install Hailo PCIe Driver on Edge Nodes

> Perform this on **node-1**, **node-2**, and **node-3** only.

1. Download the Hailo PCIe driver package from [hailo.ai/developer-zone](https://hailo.ai/developer-zone/).
2. Install it:

```bash
sudo dpkg -i hailort_<version>_arm64.deb
sudo reboot
```

3. Verify the driver loaded:

```bash
lsmod | grep hailo
ls /dev/hailo*    # should show /dev/hailo0
```

4. Install the HailoRT Python bindings:

```bash
pip install hailort-<version>-cp311-cp311-linux_aarch64.whl
```

5. Test the Hailo device:

```bash
python -c "from hailo_platform import VDevice; v = VDevice(); print('Hailo OK'); v.release()"
```

---

## Step 4 — Install Docker on the Central Server

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
docker --version
```

---

## Step 5 — Install K3s on the Central Server (Master)

```bash
# Clone the repo first
git clone https://github.com/<your-username>/traffic-analytics-cluster.git
cd traffic-analytics-cluster

# Run the master setup script
chmod +x scripts/setup-k3s-master.sh
sudo ./scripts/setup-k3s-master.sh
```

Save the **node join token** and **master IP** printed at the end.

---

## Step 6 — Install K3s Agent on Each Edge Node

SSH into each edge Pi and run:

```bash
# On node-1
sudo ./scripts/setup-k3s-agent.sh \
  --master-ip 192.168.1.100 \
  --token <paste-token-here> \
  --node-name node-1
```

Repeat for `node-2` and `node-3`.

### Label Edge Nodes

Back on the central server:

```bash
kubectl label node node-1 role=edge-camera
kubectl label node node-2 role=edge-camera
kubectl label node node-3 role=edge-camera
kubectl get nodes
```

---

## Step 7 — Prepare Models

Follow the [Model Training Guide](model-training-guide.md) to create the HEF model,
then deploy it to all edge nodes:

```bash
for NODE in node-1 node-2 node-3; do
  ssh pi@${NODE} "mkdir -p /opt/traffic-models /opt/traffic-config"
  scp models/yolov8n-traffic.hef  pi@${NODE}:/opt/traffic-models/
  scp models/yolov8n-traffic.onnx pi@${NODE}:/opt/traffic-models/
  scp -r edge-node/config/         pi@${NODE}:/opt/traffic-config/
done
```

---

## Step 8 — Configure Each Node

Edit `/opt/traffic-config/mqtt.yaml` on each Pi:

```yaml
broker_host: "192.168.1.100"
broker_port: 1883
client_id: "node-1"   # change per node
```

---

## Step 9 — Deploy Everything

On the central server:

```bash
cd traffic-analytics-cluster

# Copy and edit environment variables
cp .env.example .env
nano .env   # set INFLUXDB_TOKEN

# Build images and deploy
chmod +x scripts/deploy-all.sh
./scripts/deploy-all.sh
```

---

## Step 10 — Verify

```bash
# Check all pods are running
kubectl get pods -n traffic-analytics -w

# Check docker-compose services
docker compose ps

# Check MQTT messages arriving
mosquitto_sub -h localhost -t "city/+/health" -v

# Open Grafana dashboard
xdg-open http://localhost:3000
# Login: admin / adminpassword123
```

---

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---|---|---|
| `/dev/hailo0` not found | PCIe driver not loaded | `sudo modprobe hailo_pci` |
| Camera not detected | picamera2 not configured | `raspi-config` → Interface → Camera |
| MQTT not receiving messages | Firewall blocking 1883 | `sudo ufw allow 1883` |
| InfluxDB token error | Wrong token in `.env` | Re-run InfluxDB setup to get token |
| K3s pod stuck Pending | Node not labelled | `kubectl label node <name> role=edge-camera` |
| Low FPS (< 15) | Hailo not active, using CPU | Check `/dev/hailo0`, verify HEF path |

---

## Useful Commands

```bash
# K3s pod logs
kubectl logs -n traffic-analytics -l app=edge-node -f

# Restart edge pods (e.g. after config change)
kubectl rollout restart daemonset/edge-node -n traffic-analytics

# Check InfluxDB data
curl -XPOST http://localhost:8086/api/v2/query \
  -H "Authorization: Token ${INFLUXDB_TOKEN}" \
  -H "Content-Type: application/vnd.flux" \
  --data 'from(bucket:"traffic") |> range(start:-10m) |> limit(n:10)'

# MQTT live monitor
mosquitto_sub -h localhost -t "city/#" -v
```
