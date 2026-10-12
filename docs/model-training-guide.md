# Model Training Guide

This guide covers the full ML pipeline for the traffic detection model:
from dataset preparation through to a Hailo HEF file running at < 20 ms
per frame on the Hailo-8L.

---

## Overview

```
UA-DETRAC + COCO dataset
         │
         ▼ (relabel to 6 classes)
  traffic.yaml dataset
         │
         ▼ (YOLOv8n fine-tuning, 100 epochs)
    best.pt (PyTorch)
         │
         ▼ (Ultralytics export)
  yolov8n-traffic.onnx
         │
         ▼ (Hailo DFC: parse → optimise → compile)
  yolov8n-traffic.hef  ← deploys to Hailo-8L on each Pi
```

---

## Environment Setup

Use a machine with a **GPU** for training (NVIDIA recommended). The Hailo DFC
compilation can be done on a CPU-only machine.

```bash
# Python 3.10 (DFC requires 3.8–3.10; training works on 3.11)
python -m venv venv-train
source venv-train/bin/activate

pip install ultralytics==8.0.196 torch torchvision
pip install roboflow opencv-python PyYAML
```

---

## Dataset Preparation

### Option A — Download via Roboflow (Easiest)

Roboflow hosts traffic datasets already in YOLO format.

```python
from roboflow import Roboflow

rf = Roboflow(api_key="YOUR_API_KEY")
project = rf.workspace("roboflow-universe-projects").project("vehicles-q0x2v")
dataset = project.version(2).download("yolov8")
```

### Option B — UA-DETRAC + COCO (Most Accurate)

1. **Download UA-DETRAC:**
   ```bash
   wget https://detrac-db.rit.albany.edu/Data/DETRAC-train-data.tar.bz2
   tar -xjf DETRAC-train-data.tar.bz2 -C data/raw/
   ```

2. **Download COCO 2017:**
   ```bash
   wget http://images.cocodataset.org/zips/train2017.zip
   wget http://images.cocodataset.org/annotations/annotations_trainval2017.zip
   unzip train2017.zip -d data/raw/coco/
   unzip annotations_trainval2017.zip -d data/raw/coco/
   ```

3. **Convert annotations to YOLO format:**

   Map COCO class IDs to our 6-class schema:

   | COCO class | Our class ID | Our label |
   |---|---|---|
   | 2 (car) | 0 | car |
   | 7 (truck) | 1 | truck |
   | 5 (bus) | 2 | bus |
   | 3 (motorcycle) | 3 | motorcycle |
   | 1 (bicycle) | 4 | bicycle |
   | 0 (person) | 5 | pedestrian |

   Use a conversion script or [json2yolo](https://github.com/ultralytics/JSON2YOLO).

### Dataset YAML (`data/traffic.yaml`)

```yaml
path: ./data/traffic
train: images/train
val: images/val
test: images/test   # optional

nc: 6
names:
  - car
  - truck
  - bus
  - motorcycle
  - bicycle
  - pedestrian
```

---

## Training

```bash
yolo train \
  model=yolov8n.pt \
  data=data/traffic.yaml \
  epochs=100 \
  imgsz=640 \
  batch=16 \
  lr0=0.01 \
  lrf=0.01 \
  momentum=0.937 \
  weight_decay=0.0005 \
  warmup_epochs=3 \
  box=7.5 \
  cls=0.5 \
  dfl=1.5 \
  device=0 \
  workers=8 \
  project=runs/train \
  name=yolov8n-traffic \
  save_period=10
```

### Expected Metrics (on UA-DETRAC + COCO traffic mix)

| Metric | Target |
|---|---|
| mAP50 | ≥ 0.75 |
| mAP50-95 | ≥ 0.55 |
| Inference (Hailo-8L) | ≤ 20 ms |
| Inference (Pi 5 CPU) | ~120 ms |

### Training with Toy Cars (for Physical Demo)

If your physical demo uses toy cars, collect 200–500 photos of your toy car setup
and add them to the training set. Fine-tune from the existing best.pt weights:

```bash
yolo train \
  model=runs/train/yolov8n-traffic/weights/best.pt \
  data=data/toy_traffic.yaml \
  epochs=30 \
  imgsz=640 \
  batch=8 \
  lr0=0.001 \
  freeze=10    # freeze first 10 backbone layers
```

---

## Export to ONNX

```bash
yolo export \
  model=runs/train/yolov8n-traffic/weights/best.pt \
  format=onnx \
  imgsz=640 \
  opset=11 \
  simplify=True \
  dynamic=False

# Copy to models/
cp runs/train/yolov8n-traffic/weights/best.onnx models/yolov8n-traffic.onnx
```

Verify the ONNX model:

```python
import onnxruntime as ort
import numpy as np

sess = ort.InferenceSession("models/yolov8n-traffic.onnx")
dummy = np.random.randn(1, 3, 640, 640).astype(np.float32)
out = sess.run(None, {sess.get_inputs()[0].name: dummy})
print("Output shape:", out[0].shape)   # Expected: (1, 84, 8400)
```

---

## Convert to Hailo HEF

See [models/README.md](../models/README.md) for the full HEF conversion steps,
or use the convenience script:

```bash
./scripts/convert-model-to-hef.sh
```

### Calibration Dataset for Quantisation

A calibration dataset of ~100 representative images dramatically improves
post-quantisation accuracy. Collect frames from your camera:

```bash
mkdir -p models/calibration_data
# Extract 1 frame per second from a 2-minute recording
ffmpeg -i camera-recording.mp4 \
  -vf "fps=1,scale=640:640" \
  -q:v 2 \
  models/calibration_data/frame_%04d.jpg
```

---

## Benchmarking on Raspberry Pi 5 + Hailo-8L

After deploying the HEF to a Pi, benchmark inference speed:

```bash
# SSH onto an edge Pi
ssh pi@node-1

# Hailo benchmark tool (from hailort package)
hailortcli benchmark \
  --hef /opt/traffic-models/yolov8n-traffic.hef \
  --batch 1 \
  --target HAILO8L

# Expected output:
# Throughput: ~50 FPS (single stream)
# Latency: ~18 ms
```

---

## Accuracy Validation

```bash
yolo val \
  model=runs/train/yolov8n-traffic/weights/best.pt \
  data=data/traffic.yaml \
  imgsz=640 \
  batch=32 \
  device=0
```

After HEF conversion, compare accuracy against the original ONNX model:

```bash
yolo val \
  model=models/yolov8n-traffic.onnx \
  data=data/traffic.yaml \
  imgsz=640
```

Expect a 1–3% mAP drop from INT8 quantisation — acceptable for real-time tracking.
