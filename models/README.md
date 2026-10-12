# Models Directory

This directory holds the AI model files used by the edge nodes for object detection.

> **Note:** Model binaries (`.hef`, `.onnx`, `.pt`) are excluded from Git via `.gitignore`
> because they are large (50–100 MB). Store them with Git LFS, an S3 bucket, or
> copy them directly to each Pi via `scp`.

---

## Required Files

| File | Size | Purpose |
|---|---|---|
| `yolov8n-traffic.hef` | ~10 MB | Hailo-compiled model for Hailo-8L inference |
| `yolov8n-traffic.onnx` | ~6 MB | ONNX model for CPU/CUDA fallback inference |

---

## Detection Classes

| Class ID | Label | Dataset Source |
|---|---|---|
| 0 | car | UA-DETRAC + COCO |
| 1 | truck | UA-DETRAC + COCO |
| 2 | bus | UA-DETRAC + COCO |
| 3 | motorcycle | COCO |
| 4 | bicycle | COCO |
| 5 | pedestrian | COCO |

---

## Option A — Use a Pre-trained Model (Fastest)

If you don't need custom classes, you can use the stock YOLOv8n model pre-converted
for Hailo from the [Hailo Model Zoo](https://github.com/hailo-ai/hailo_model_zoo):

```bash
# Clone the Hailo Model Zoo
git clone https://github.com/hailo-ai/hailo_model_zoo
cd hailo_model_zoo

# Download YOLOv8n pre-compiled HEF for Hailo-8L
python hailo_model_zoo/main.py download yolov8n

# The HEF will be in: hailo_model_zoo/hef/yolov8n.hef
cp hailo_model_zoo/hef/yolov8n.hef ../models/yolov8n-traffic.hef
```

Note: The stock YOLOv8n uses 80 COCO classes. To use it with this project,
either remap the class IDs in `config/detection.yaml` or fine-tune (see below).

---

## Option B — Fine-tune on Traffic Data (Best Accuracy)

### 1. Install Ultralytics

```bash
pip install ultralytics==8.0.196
```

### 2. Download Datasets

**UA-DETRAC** (vehicles):
```bash
# Download from: https://detrac-db.rit.albany.edu/
wget https://detrac-db.rit.albany.edu/Data/DETRAC-train-data.tar.bz2
tar -xjf DETRAC-train-data.tar.bz2
```

**COCO subset** (pedestrians, cyclists):
```bash
# Download COCO 2017 val set (for pedestrian/cyclist classes)
wget http://images.cocodataset.org/zips/val2017.zip
unzip val2017.zip
```

### 3. Prepare Dataset in YOLO Format

Create `data/traffic.yaml`:

```yaml
path: ./data/traffic
train: images/train
val: images/val
nc: 6
names: [car, truck, bus, motorcycle, bicycle, pedestrian]
```

Convert UA-DETRAC and COCO annotations to YOLO format using the provided
script in `scripts/` (or use Roboflow for automated conversion).

### 4. Fine-tune YOLOv8-nano

```bash
yolo train \
  model=yolov8n.pt \
  data=data/traffic.yaml \
  epochs=100 \
  imgsz=640 \
  batch=16 \
  name=yolov8n-traffic \
  project=runs/train
```

Training time: ~4 hours on a GPU (RTX 3060 or better recommended).

### 5. Export to ONNX

```bash
yolo export \
  model=runs/train/yolov8n-traffic/weights/best.pt \
  format=onnx \
  imgsz=640 \
  opset=11 \
  simplify=True

cp runs/train/yolov8n-traffic/weights/best.onnx models/yolov8n-traffic.onnx
```

---

## Option C — Convert to Hailo HEF

After exporting the ONNX model, convert it to HEF format for Hailo-8L acceleration.

Use the provided script:

```bash
chmod +x scripts/convert-model-to-hef.sh
./scripts/convert-model-to-hef.sh
```

Or manually with the Hailo DFC:

```bash
# Step 1: Parse ONNX to HAR
hailo parser onnx models/yolov8n-traffic.onnx \
  --net-name yolov8n-traffic \
  --hw-arch hailo8l \
  --har-path models/yolov8n-traffic.har

# Step 2: Optimise / quantise (requires calibration images)
hailo optimize models/yolov8n-traffic.har \
  --hw-arch hailo8l \
  --calib-path models/calibration_data/ \
  --output-har-path models/yolov8n-traffic-quantized.har

# Step 3: Compile to HEF
hailo compiler models/yolov8n-traffic-quantized.har \
  --hw-arch hailo8l \
  --output-dir models/
```

**Prerequisites:**
- Hailo Dataflow Compiler (DFC) ≥ 3.28 ([download from hailo.ai](https://hailo.ai/developer-zone/software-downloads/))
- Python 3.8–3.10 (DFC requirement)
- 16 GB RAM recommended for compilation

---

## Calibration Data

Create `models/calibration_data/` and add 50–100 representative images
(frames from your camera setup showing typical traffic). The calibration
dataset is used to calibrate quantisation for minimal accuracy loss.

```bash
mkdir -p models/calibration_data
# Copy ~100 representative traffic frames:
# ffmpeg -i video.mp4 -vf fps=1 -q:v 2 models/calibration_data/frame_%04d.jpg
```

---

## Deploy Models to Edge Nodes

After building the HEF file, copy it to each Pi:

```bash
NODES=(node-1 node-2 node-3 node-4)
for NODE in "${NODES[@]}"; do
  echo "Copying model to ${NODE}..."
  ssh pi@${NODE} "mkdir -p /opt/traffic-models"
  scp models/yolov8n-traffic.hef pi@${NODE}:/opt/traffic-models/
  scp models/yolov8n-traffic.onnx pi@${NODE}:/opt/traffic-models/
done
echo "Done."
```
