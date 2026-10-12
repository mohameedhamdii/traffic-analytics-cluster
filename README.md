# 🚦 Smart-City Traffic & Parking Analytics Cluster

> **Real-time multi-camera vehicle tracking, pedestrian detection, and parking occupancy monitoring
> on a distributed Raspberry Pi 5 + Hailo-8L edge AI cluster.**

[![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![K3s](https://img.shields.io/badge/K3s-Kubernetes-orange?logo=kubernetes)](https://k3s.io)
[![Grafana](https://img.shields.io/badge/Grafana-Dashboard-orange?logo=grafana)](https://grafana.com)
[![InfluxDB](https://img.shields.io/badge/InfluxDB-2.x-purple?logo=influxdb)](https://influxdata.com)
[![MQTT](https://img.shields.io/badge/MQTT-Mosquitto-red?logo=mqtt)](https://mosquitto.org)

---

## 📌 Project Summary

A **distributed edge computing cluster** built with 3–4 Raspberry Pi 5 nodes,
each running a camera + Hailo-8L AI accelerator to perform **real-time object detection
and multi-object tracking** at ≥ 25 FPS per node. All nodes communicate over a
gigabit LAN via MQTT, feed a central InfluxDB time-series database, and visualise
live traffic flow, parking occupancy, and node health on a Grafana dashboard.

**Demonstrates:**
- Distributed systems engineering (multi-node K3s cluster)
- Edge AI acceleration (Hailo-8L, YOLOv8-nano at < 20 ms/frame)
- Real-time multi-object tracking (ByteTrack with Kalman filter)
- Data pipeline architecture (MQTT → InfluxDB → Grafana)
- Production-grade containerisation (Docker + K3s DaemonSets)

---

## 🏗️ System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    EDGE NODE (×3–4)                             │
│  ┌──────────┐   ┌──────────────┐   ┌────────────────────┐      │
│  │ Pi Camera │──▶│ YOLOv8-nano  │──▶│ ByteTrack/Kalman   │      │
│  │ (capture) │   │ (Hailo-8L)   │   │ (multi-obj track)  │      │
│  └──────────┘   └──────────────┘   └────────┬───────────┘      │
│                                              │ track data       │
│                                              ▼                  │
│                                    ┌─────────────────┐          │
│                                    │  MQTT Publisher  │          │
│                                    │  city/node-N/…   │          │
│                                    └─────────────────┘          │
└──────────────────────────────────────┬──────────────────────────┘
                                       │  MQTT over Gigabit LAN
                                       ▼
┌─────────────────────────────────────────────────────────────────┐
│                    CENTRAL SERVER                               │
│  ┌───────────────┐   ┌──────────┐   ┌─────────────────┐        │
│  │ MQTT Subscriber│──▶│Aggregator│──▶│   InfluxDB 2.x  │        │
│  └───────────────┘   └──────────┘   └────────┬────────┘        │
│                                               ▼                 │
│                                      ┌──────────────┐           │
│                                      │   Grafana     │           │
│                                      │  Dashboard    │           │
│                                      └──────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Edge compute | Raspberry Pi 5 (8 GB) |
| AI accelerator | Hailo-8L M.2 via Pi AI HAT |
| Camera | Raspberry Pi Camera Module 3 (wide) |
| Object detection | YOLOv8-nano (`.hef` Hailo / `.onnx` CPU fallback) |
| Multi-object tracking | ByteTrack with Kalman filter (pure Python, no Re-ID) |
| Edge analytics | Virtual counting lines, speed estimation, parking ROIs |
| Messaging | MQTT (Eclipse Mosquitto 2.x) |
| Time-series DB | InfluxDB 2.7 (Flux query language) |
| Dashboard | Grafana 10 |
| Orchestration | K3s (lightweight Kubernetes) |
| Containers | Docker |
| Language | Python 3.11 |

---

## 📂 Repository Structure

```
traffic-analytics-cluster/
├── README.md
├── docker-compose.yaml              # Central server services
├── .env.example                     # Environment variable template
├── .gitignore
│
├── edge-node/                       # Code running on each Pi camera node
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py                      # Pipeline entry point
│   ├── camera_service.py            # picamera2 capture (OpenCV fallback)
│   ├── detector.py                  # YOLOv8 inference (Hailo + ONNX)
│   ├── tracker.py                   # ByteTrack multi-object tracker
│   ├── analytics.py                 # Counting, speed, parking occupancy
│   ├── mqtt_publisher.py            # MQTT publisher with auto-reconnect
│   └── config/
│       ├── camera.yaml
│       ├── mqtt.yaml
│       ├── detection.yaml
│       ├── tracker.yaml
│       ├── counting_lines.yaml
│       └── parking_spots.yaml
│
├── central-server/                  # Aggregator service
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── aggregator.py                # MQTT→InfluxDB pipeline + alerting
│   └── config/
│       ├── influxdb.yaml
│       └── mqtt.yaml
│
├── grafana/                         # Dashboard provisioning
│   ├── provisioning/
│   │   ├── datasources/influxdb.yaml
│   │   └── dashboards/dashboard-provider.yaml
│   └── dashboards/
│       └── traffic-dashboard.json  # All 6 panels pre-configured
│
├── k3s/                             # Kubernetes manifests
│   ├── namespace.yaml
│   ├── edge-node-deployment.yaml   # DaemonSet (1 pod per edge Pi)
│   └── central-services.yaml       # Aggregator deployment
│
├── mosquitto/config/mosquitto.conf  # MQTT broker configuration
│
├── models/                          # AI model files (excluded from git)
│   └── README.md                    # Training & conversion instructions
│
├── scripts/
│   ├── setup-k3s-master.sh
│   ├── setup-k3s-agent.sh
│   ├── deploy-all.sh
│   └── convert-model-to-hef.sh
│
├── tests/
│   ├── conftest.py
│   ├── test_detector.py
│   ├── test_tracker.py
│   ├── test_analytics.py
│   └── test_aggregator.py
│
└── docs/
    ├── cluster-setup-guide.md       # Step-by-step hardware setup
    └── model-training-guide.md      # YOLOv8 training & HEF conversion
```

---

## 🚀 Quick Start

### Prerequisites

- Docker & Docker Compose installed on the central server
- Python 3.11 for local development / testing

### 1. Clone the Repository

```bash
git clone https://github.com/<your-username>/traffic-analytics-cluster.git
cd traffic-analytics-cluster
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env and set a strong INFLUXDB_TOKEN
nano .env
```

### 3. Start Central Services (Development Mode)

```bash
docker compose up -d
```

This starts:
- **Mosquitto MQTT broker** on port `1883` (WebSocket: `9001`)
- **InfluxDB 2.7** on port `8086`
- **Grafana 10** on port `3000`
- **Aggregator** (subscribes to MQTT, writes to InfluxDB)

### 4. Open the Dashboard

Navigate to **http://localhost:3000** and log in with `admin / adminpassword123`.

The **"Smart City Traffic & Parking Analytics"** dashboard is pre-provisioned
in the **Smart City Traffic** folder.

### 5. Simulate Data (No Hardware Required)

```bash
pip install paho-mqtt
python - <<'EOF'
import paho.mqtt.client as mqtt
import json, time, random

client = mqtt.Client()
client.connect("localhost", 1883)

for i in range(60):
    client.publish("city/node-1/counts", json.dumps({
        "node_id": "node-1", "timestamp": time.time(),
        "counts": {"car": random.randint(0, 10), "pedestrian": random.randint(0, 5)},
        "total": random.randint(0, 15)
    }), qos=1)
    client.publish("city/node-1/health", json.dumps({
        "node_id": "node-1", "status": "online",
        "timestamp": time.time(), "fps": random.uniform(25, 30),
        "cpu_temp_c": random.uniform(45, 65), "active_tracks": random.randint(0, 8)
    }), qos=1)
    time.sleep(5)
EOF
```

---

## 🍓 Deploying to Real Hardware

See the full **[Cluster Setup Guide](docs/cluster-setup-guide.md)** for
step-by-step hardware assembly through to a live cluster.

**Summary:**

```bash
# On central server — install K3s master
sudo ./scripts/setup-k3s-master.sh

# On each edge Pi — install K3s agent
sudo ./scripts/setup-k3s-agent.sh \
  --master-ip 192.168.1.100 \
  --token <token> \
  --node-name node-1

# Deploy all workloads
./scripts/deploy-all.sh
```

---

## 🧠 ML Model

The edge detector uses **YOLOv8-nano** fine-tuned on a traffic dataset and compiled
to **Hailo HEF format** for the Hailo-8L accelerator.

See the **[Model Training Guide](docs/model-training-guide.md)** and
**[models/README.md](models/README.md)** for training, ONNX export,
and Hailo HEF conversion instructions.

**No Hailo hardware?** The system automatically falls back to **OpenCV DNN
ONNX inference on CPU** — functional but ~5× slower.

---

## 📊 Grafana Dashboard Panels

| Panel | Type | Data Source |
|---|---|---|
| Total Vehicles (last min) | Stat card | `vehicle_count` measurement |
| Nodes Online | Stat card | `node_health` measurement |
| Average Speed | Gauge | `avg_speed` measurement |
| Parking Occupancy % | Gauge | `parking_summary` measurement |
| Vehicle Counts by Class | Time series | `vehicle_count` |
| Average Speed per Node | Time series | `avg_speed` |
| Parking Spot Grid | Table (colour-coded) | `parking_occupancy` |
| Parking Occupancy Rate | Time series | `parking_summary` |
| Node Health Table | Table (colour-coded) | `node_health` |
| Inference FPS per Node | Time series | `node_health` |
| CPU Temperature per Node | Time series | `node_health` |
| Historical Traffic Heatmap | Heatmap | `vehicle_count` hourly |

---

## 🧪 Running Tests

```bash
# Install test dependencies
pip install pytest paho-mqtt influxdb-client PyYAML numpy scipy

# Run all tests
python -m pytest tests/ -v

# Run a specific test file
python -m pytest tests/test_tracker.py -v

# Run with coverage
pip install pytest-cov
python -m pytest tests/ --cov=edge-node --cov=central-server --cov-report=html
```

---

## 🔌 MQTT Topic Schema

| Topic | QoS | Payload | Frequency |
|---|---|---|---|
| `city/<node_id>/tracks` | 0 | Raw track list | Every 5th frame |
| `city/<node_id>/counts` | 1 | Per-class vehicle counts | Every minute |
| `city/<node_id>/parking` | 1 | Spot occupancy map (retained) | On change |
| `city/<node_id>/speed` | 1 | Average vehicle speed | Every minute |
| `city/<node_id>/health` | 1 | FPS, CPU temp, online status (retained) | Every 10 s |
| `city/alerts` | 1 | Parking-full / node-offline alerts | On event |

---

## ⚙️ Configuration

All configuration is YAML-based and lives in `edge-node/config/`. Key files:

| File | Controls |
|---|---|
| `camera.yaml` | Resolution, FPS, rotation, backend |
| `detection.yaml` | Confidence threshold, NMS, model paths |
| `tracker.yaml` | Max age, min hits, IoU threshold |
| `counting_lines.yaml` | Virtual line positions and directions |
| `parking_spots.yaml` | Parking bay ROI coordinates |
| `mqtt.yaml` | Broker IP, port, node ID |

All values can be overridden via environment variables (see `.env.example`).

---

## 🔮 Stretch Goals

- [ ] Cross-camera Re-ID (DeepSORT appearance features)
- [ ] Anomaly detection (stopped vehicles, pedestrians in roadway)
- [ ] Predictive parking (time-series forecasting with InfluxDB tasks)
- [ ] Live RTSP → HLS video stream with bounding box overlay
- [ ] Prometheus + Alertmanager for production-grade monitoring

---

## 📋 Estimated Build Timeline

| Phase | Duration |
|---|---|
| Hardware setup & cluster networking | 2 days |
| YOLOv8 training & Hailo model conversion | 3 days |
| Edge node pipeline (capture → detect → track) | 4 days |
| MQTT + aggregator + InfluxDB pipeline | 3 days |
| Grafana dashboard | 2 days |
| Multi-node K3s deployment | 2 days |
| Testing, tuning, documentation | 2 days |
| **Total** | **~18 days** |

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🙏 Acknowledgements

- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics)
- [ByteTrack](https://github.com/ifzhang/ByteTrack) — Zhang et al. 2022
- [Hailo Model Zoo](https://github.com/hailo-ai/hailo_model_zoo)
- [Eclipse Mosquitto](https://mosquitto.org)
- [InfluxDB](https://influxdata.com) / [Grafana](https://grafana.com)
- [K3s by Rancher](https://k3s.io)
