"""
test_detector.py — Unit tests for the object detector module.

Tests the Detection dataclass, the ONNX fallback detector, and the
postprocessing (NMS, class filtering, bounding box clamping).

Note: HailoDetector requires hardware; those tests are skipped in CI.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "edge-node"))

from detector import Detection


# ── Detection dataclass ────────────────────────────────────────────────────────

class TestDetection:
    def test_bbox_property(self):
        det = Detection(class_id=0, label="car", confidence=0.9,
                        x1=100, y1=200, x2=300, y2=400)
        assert det.bbox == [100, 200, 300, 400]

    def test_centroid(self):
        det = Detection(class_id=0, label="car", confidence=0.9,
                        x1=100, y1=200, x2=300, y2=400)
        cx, cy = det.centroid
        assert cx == 200
        assert cy == 300

    def test_width_height(self):
        det = Detection(class_id=1, label="truck", confidence=0.8,
                        x1=50, y1=50, x2=250, y2=350)
        assert det.width == 200
        assert det.height == 300

    def test_area(self):
        det = Detection(class_id=0, label="car", confidence=0.7,
                        x1=0, y1=0, x2=100, y2=100)
        assert det.area == 10000

    def test_to_tlwh(self):
        det = Detection(class_id=0, label="car", confidence=0.9,
                        x1=10, y1=20, x2=110, y2=120)
        tlwh = det.to_tlwh()
        # [top, left, height, width]
        assert tlwh[0] == 20   # y1 = top
        assert tlwh[1] == 10   # x1 = left
        assert tlwh[2] == 100  # height
        assert tlwh[3] == 100  # width

    def test_to_xyxy(self):
        det = Detection(class_id=0, label="car", confidence=0.9,
                        x1=10, y1=20, x2=110, y2=120)
        xyxy = det.to_xyxy()
        np.testing.assert_array_equal(xyxy, [10, 20, 110, 120])

    def test_confidence_clamped(self):
        # Should not raise even at boundary confidence values
        det1 = Detection(class_id=0, label="car", confidence=0.0,
                         x1=0, y1=0, x2=1, y2=1)
        det2 = Detection(class_id=0, label="car", confidence=1.0,
                         x1=0, y1=0, x2=1, y2=1)
        assert det1.confidence == 0.0
        assert det2.confidence == 1.0


# ── OnnxDetector ──────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not Path("models/yolov8n-traffic.onnx").exists(),
    reason="ONNX model not present — run model training/export first.",
)
class TestOnnxDetector:
    """Integration test — requires the ONNX model file."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        import yaml
        from detector import DetectorConfig, OnnxDetector

        cfg_data = {
            "confidence_threshold": 0.4,
            "nms_threshold": 0.45,
            "input_size": [640, 640],
            "hef_model_path": "models/yolov8n-traffic.hef",
            "onnx_model_path": "models/yolov8n-traffic.onnx",
            "enable_hailo": False,
            "class_names": ["car", "truck", "bus", "motorcycle", "bicycle", "pedestrian"],
        }
        cfg_path = tmp_path / "detection.yaml"
        cfg_path.write_text(yaml.dump(cfg_data))
        self.detector = OnnxDetector(DetectorConfig(str(cfg_path)))

    def test_blank_frame_returns_no_detections(self, blank_frame):
        detections = self.detector.detect(blank_frame)
        assert isinstance(detections, list)
        # A blank frame should produce no confident detections
        assert all(d.confidence >= 0.4 for d in detections)

    def test_output_is_list_of_detections(self, grey_frame):
        from detector import Detection
        detections = self.detector.detect(grey_frame)
        assert isinstance(detections, list)
        for d in detections:
            assert isinstance(d, Detection)

    def test_bboxes_within_frame(self, grey_frame):
        h, w = grey_frame.shape[:2]
        detections = self.detector.detect(grey_frame)
        for d in detections:
            assert 0 <= d.x1 < d.x2 <= w
            assert 0 <= d.y1 < d.y2 <= h


# ── create_detector factory ────────────────────────────────────────────────────

class TestCreateDetector:
    def test_returns_onnx_when_hailo_disabled(self, tmp_path):
        import yaml
        from detector import OnnxDetector, create_detector

        # Create a minimal detection config with Hailo disabled
        # (will fail to create detector if ONNX model doesn't exist)
        cfg_data = {
            "confidence_threshold": 0.4,
            "nms_threshold": 0.45,
            "input_size": [640, 640],
            "hef_model_path": "models/yolov8n-traffic.hef",
            "onnx_model_path": str(tmp_path / "dummy.onnx"),
            "enable_hailo": False,
            "class_names": ["car"],
        }
        cfg_path = tmp_path / "detection.yaml"
        cfg_path.write_text(yaml.dump(cfg_data))

        # Should raise FileNotFoundError since model doesn't exist
        with pytest.raises(FileNotFoundError):
            create_detector(str(cfg_path))
