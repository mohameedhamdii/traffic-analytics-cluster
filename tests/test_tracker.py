"""
test_tracker.py — Unit tests for the ByteTrack multi-object tracker.

Validates:
  - Kalman filter predict / update mechanics
  - IoU computation
  - Track creation on first frame
  - Track ID persistence across frames
  - Track aging and deletion
  - Two-stage matching
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "edge-node"))

from tracker import ByteTracker, KalmanBoxTracker, TrackState, iou_batch


# ── KalmanBoxTracker ──────────────────────────────────────────────────────────

class TestKalmanBoxTracker:
    def test_predict_returns_bbox(self):
        bbox = np.array([100, 200, 300, 400], dtype=np.float32)
        kf = KalmanBoxTracker(bbox)
        pred = kf.predict()
        assert pred.shape == (4,)
        # Predicted bbox should be close to initial (no motion yet)
        np.testing.assert_allclose(pred, bbox, atol=5.0)

    def test_update_corrects_state(self):
        bbox = np.array([100, 200, 300, 400], dtype=np.float32)
        kf = KalmanBoxTracker(bbox)
        # Move the bbox 50 pixels right
        new_bbox = np.array([150, 200, 350, 400], dtype=np.float32)
        kf.update(new_bbox)
        updated = kf.get_bbox()
        # After update, bbox should be closer to new_bbox
        assert updated[0] > 100  # x1 moved right

    def test_unique_ids_incremented(self):
        KalmanBoxTracker.count = 0
        kf1 = KalmanBoxTracker(np.array([0, 0, 100, 100], dtype=np.float32))
        kf2 = KalmanBoxTracker(np.array([0, 0, 100, 100], dtype=np.float32))
        assert kf2.track_id > kf1.track_id


# ── IoU computation ────────────────────────────────────────────────────────────

class TestIoU:
    def test_identical_boxes_iou_is_one(self):
        boxes = np.array([[0, 0, 100, 100]], dtype=np.float32)
        iou = iou_batch(boxes, boxes)
        assert iou.shape == (1, 1)
        np.testing.assert_allclose(iou[0, 0], 1.0, atol=1e-5)

    def test_non_overlapping_boxes_iou_is_zero(self):
        a = np.array([[0, 0, 50, 50]], dtype=np.float32)
        b = np.array([[100, 100, 200, 200]], dtype=np.float32)
        iou = iou_batch(a, b)
        np.testing.assert_allclose(iou[0, 0], 0.0, atol=1e-5)

    def test_half_overlap(self):
        a = np.array([[0, 0, 100, 100]], dtype=np.float32)
        b = np.array([[50, 0, 150, 100]], dtype=np.float32)
        iou = iou_batch(a, b)
        # Intersection = 50×100 = 5000, Union = 2×10000 - 5000 = 15000
        expected = 5000 / 15000
        np.testing.assert_allclose(iou[0, 0], expected, atol=1e-4)

    def test_empty_arrays(self):
        a = np.zeros((0, 4), dtype=np.float32)
        b = np.array([[0, 0, 100, 100]], dtype=np.float32)
        iou = iou_batch(a, b)
        assert iou.shape == (0, 1)


# ── ByteTracker ────────────────────────────────────────────────────────────────

class TestByteTracker:
    @pytest.fixture(autouse=True)
    def reset_id_counter(self):
        """Reset KalmanBoxTracker ID counter before each test."""
        KalmanBoxTracker.count = 0

    def test_single_detection_creates_one_track(self, tracker, sample_detection):
        tracks = tracker.update([sample_detection])
        # min_hits=1, so track should be confirmed immediately
        assert len(tracks) == 1
        assert tracks[0].class_id == 0
        assert tracks[0].label == "car"

    def test_track_id_persists_across_frames(self, tracker, sample_detection):
        """The same object should get the same track ID across consecutive frames."""
        tracks1 = tracker.update([sample_detection])
        assert len(tracks1) == 1
        tid = tracks1[0].track_id

        # Second frame — same position (high IoU, should match)
        tracks2 = tracker.update([sample_detection])
        assert len(tracks2) == 1
        assert tracks2[0].track_id == tid

    def test_moving_object_maintains_id(self, tracker, sample_detections_sequence):
        """Track ID should persist for a car moving across 10 frames."""
        first_tracks = tracker.update(sample_detections_sequence[0])
        assert len(first_tracks) == 1
        original_tid = first_tracks[0].track_id

        for dets in sample_detections_sequence[1:]:
            tracks = tracker.update(dets)
            if tracks:
                assert tracks[0].track_id == original_tid

    def test_track_deleted_after_max_age(self, tracker, sample_detection):
        """A track should be deleted if it isn't matched for max_age frames."""
        tracker.update([sample_detection])

        # Send frames with no detections
        for _ in range(tracker.max_age + 2):
            remaining = tracker.update([])

        # The track should have been deleted
        assert len(remaining) == 0

    def test_two_separate_objects_get_different_ids(self, tracker):
        """Two non-overlapping detections should produce two distinct tracks."""
        from detector import Detection

        det_a = Detection(class_id=0, label="car", confidence=0.9,
                          x1=0, y1=0, x2=100, y2=100)
        det_b = Detection(class_id=0, label="car", confidence=0.9,
                          x1=500, y1=500, x2=600, y2=600)

        tracks = tracker.update([det_a, det_b])
        assert len(tracks) == 2
        assert tracks[0].track_id != tracks[1].track_id

    def test_low_confidence_detection_does_not_create_new_track(self):
        """Low-confidence detections below low_score_threshold should be ignored."""
        from detector import Detection

        tracker = ByteTracker(
            max_age=10,
            min_hits=1,
            high_score_threshold=0.6,
            low_score_threshold=0.4,
        )
        # Confidence below low_score_threshold
        det = Detection(class_id=0, label="car", confidence=0.1,
                        x1=100, y1=100, x2=200, y2=200)
        tracks = tracker.update([det])
        assert len(tracks) == 0

    def test_confirmed_state_after_min_hits(self):
        """Track should be CONFIRMED after min_hits successful matches."""
        from detector import Detection

        tracker = ByteTracker(
            max_age=30, min_hits=3, iou_threshold=0.3, high_score_threshold=0.5
        )
        det = Detection(class_id=0, label="car", confidence=0.8,
                        x1=100, y1=100, x2=200, y2=200)

        for _ in range(3):
            tracks = tracker.update([det])

        assert any(t.state == TrackState.CONFIRMED for t in tracks)

    def test_tracker_reset(self, tracker, sample_detection):
        tracker.update([sample_detection])
        tracker.reset()
        assert tracker.track_count == 0

    def test_empty_frame_no_error(self, tracker):
        tracks = tracker.update([])
        assert tracks == []
