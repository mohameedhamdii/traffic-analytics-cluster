#!/usr/bin/env bash
# =============================================================================
# convert-model-to-hef.sh — Convert a YOLOv8-nano ONNX model to Hailo HEF
#
# Prerequisites:
#   - Hailo Dataflow Compiler (DFC) installed (available from hailo.ai)
#   - Python environment with hailo_model_zoo and hailo_sdk_client
#   - Input: models/yolov8n-traffic.onnx (exported from Ultralytics)
#
# Usage:
#   chmod +x convert-model-to-hef.sh && ./convert-model-to-hef.sh
#
# Reference:
#   https://hailo.ai/developer-zone/documentation/hailo-model-zoo/
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="${REPO_ROOT}/models"
ONNX_MODEL="${MODELS_DIR}/yolov8n-traffic.onnx"
HAR_FILE="${MODELS_DIR}/yolov8n-traffic.har"
HEF_FILE="${MODELS_DIR}/yolov8n-traffic.hef"
HW_ARCH="hailo8l"           # Hailo-8L on Raspberry Pi AI HAT
CALIBRATION_DIR="${MODELS_DIR}/calibration_data"

echo "======================================================================"
echo "  YOLOv8-nano ONNX → Hailo HEF Conversion"
echo "  Target hardware: ${HW_ARCH}"
echo "======================================================================"

# ── Check prerequisites ──────────────────────────────────────────────────────
if [[ ! -f "${ONNX_MODEL}" ]]; then
  echo "❌ ONNX model not found: ${ONNX_MODEL}"
  echo "   Export it from Ultralytics first:"
  echo "   yolo export model=best.pt format=onnx imgsz=640 opset=11"
  exit 1
fi

if ! command -v hailo &>/dev/null && ! python -c "import hailo_sdk_client" &>/dev/null 2>&1; then
  echo "❌ Hailo DFC not found. Install from:"
  echo "   https://hailo.ai/developer-zone/software-downloads/"
  exit 1
fi

echo ""
echo "[1/4] Parsing ONNX model to Hailo archive (.har)..."
hailo parser onnx \
  "${ONNX_MODEL}" \
  --net-name yolov8n-traffic \
  --hw-arch "${HW_ARCH}" \
  --har-path "${HAR_FILE}"

echo ""
echo "[2/4] Optimising / quantising model (requires calibration dataset)..."
echo "  Using calibration images from: ${CALIBRATION_DIR}"
echo "  If directory is empty, create it and add ~100 representative images."

if [[ -d "${CALIBRATION_DIR}" && "$(ls -A "${CALIBRATION_DIR}" 2>/dev/null)" ]]; then
  hailo optimize \
    "${HAR_FILE}" \
    --hw-arch "${HW_ARCH}" \
    --calib-path "${CALIBRATION_DIR}" \
    --output-har-path "${MODELS_DIR}/yolov8n-traffic-quantized.har"
  HAR_FILE="${MODELS_DIR}/yolov8n-traffic-quantized.har"
else
  echo "  ⚠️  No calibration images found — using default random calibration (lower accuracy)."
  hailo optimize \
    "${HAR_FILE}" \
    --hw-arch "${HW_ARCH}" \
    --output-har-path "${MODELS_DIR}/yolov8n-traffic-quantized.har"
  HAR_FILE="${MODELS_DIR}/yolov8n-traffic-quantized.har"
fi

echo ""
echo "[3/4] Compiling to HEF..."
hailo compiler \
  "${HAR_FILE}" \
  --hw-arch "${HW_ARCH}" \
  --output-dir "${MODELS_DIR}"

echo ""
echo "[4/4] Verifying HEF file..."
if [[ -f "${HEF_FILE}" ]]; then
  echo "  ✅ HEF file created: ${HEF_FILE}"
  ls -lh "${HEF_FILE}"
else
  echo "  ❌ HEF file not found — compilation may have failed."
  echo "     Check the output above for errors."
  exit 1
fi

echo ""
echo "======================================================================"
echo "✅ Model conversion complete!"
echo "   HEF: ${HEF_FILE}"
echo ""
echo "Deploy to edge nodes:"
echo "  for NODE in node-1 node-2 node-3 node-4; do"
echo "    scp ${HEF_FILE} pi@\${NODE}:/opt/traffic-models/"
echo "  done"
echo "======================================================================"
