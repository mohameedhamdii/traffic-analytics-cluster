"""
conftest.py — Shared pytest fixtures for the traffic analytics test suite.
"""

import os
import sys
from pathlib import Path

import numpy as np
import pytest

# Add both edge-node and central-server to the path so tests can import modules
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "edge-node"))
sys.path.insert(0, str(REPO_ROOT / "central-server"))


# ── Frame fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def blank_frame():
    """Returns a blank 720p BGR frame (black image)."""
    return np.zeros((720, 1280, 3), dtype=np.uint8)


@pytest.fixture
def grey_frame():
    """Returns a uniform grey 720p BGR frame."""
    return np.full((720, 1280, 3), 128, dtype=np.uint8)


@pytest.fixture
def sample_frame_with_car():
    """
    Returns a 720p frame with a white rectangle simulating a car bounding box
    in the lower-centre of the frame.
    """
    import cv2
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Draw a white rectangle representing a car
    cv2.rectangle(frame, (540, 400), (740, 520), (255, 255, 255), -1)
    return frame


# ── Detection fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def sample_detection():
    """Returns a single car detection at a fixed position."""
    from detector import Detection
    return Detection(
        class_id=0,
        label="car",
        confidence=0.85,
        x1=540,
        y1=400,
        x2=740,
        y2=520,
    )


@pytest.fixture
def sample_detections_sequence():
    """
    Returns a list of 10 frames of detections simulating a car moving
    right across the frame (x increases each frame by 20px).
    """
    from detector import Detection

    sequence = []
    for i in range(10):
        offset = i * 20
        det = Detection(
            class_id=0,
            label="car",
            confidence=0.9,
            x1=540 + offset,
            y1=400,
            x2=740 + offset,
            y2=520,
        )
        sequence.append([det])
    return sequence


@pytest.fixture
def pedestrian_detection():
    """Returns a single pedestrian detection."""
    from detector import Detection
    return Detection(
        class_id=5,
        label="pedestrian",
        confidence=0.75,
        x1=300,
        y1=200,
        x2=340,
        y2=380,
    )


# ── Tracker fixture ────────────────────────────────────────────────────────────

@pytest.fixture
def tracker():
    """Returns a ByteTracker configured with low min_hits for easier testing."""
    from tracker import ByteTracker
    return ByteTracker(
        max_age=10,
        min_hits=1,  # Confirm tracks immediately for testing
        iou_threshold=0.3,
        high_score_threshold=0.5,
        low_score_threshold=0.1,
    )


# ── Analytics fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def analytics_engine(tmp_path):
    """
    Returns an AnalyticsEngine loaded from temporary config files
    with two counting lines and two parking spots.
    """
    import yaml
    from analytics import AnalyticsEngine

    # Create temporary counting_lines.yaml
    cl_config = {
        "counting_lines": [
            {
                "line_id": "horizontal",
                "start": [0, 400],
                "end": [1280, 400],
                "direction": "up",
            },
            {
                "line_id": "vertical",
                "start": [640, 0],
                "end": [640, 720],
                "direction": "right",
            },
        ]
    }
    cl_path = tmp_path / "counting_lines.yaml"
    cl_path.write_text(yaml.dump(cl_config))

    # Create temporary parking_spots.yaml
    ps_config = {
        "occupied_threshold_seconds": 0.1,  # very short for testing
        "parking_spots": [
            {"spot_id": "A1", "roi": [100, 300, 200, 400]},
            {"spot_id": "A2", "roi": [220, 300, 320, 400]},
        ],
    }
    ps_path = tmp_path / "parking_spots.yaml"
    ps_path.write_text(yaml.dump(ps_config))

    return AnalyticsEngine.from_configs(
        counting_lines_path=str(cl_path),
        parking_spots_path=str(ps_path),
    )
