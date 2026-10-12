"""
test_analytics.py — Unit tests for the analytics engine.

Validates:
  - Counting line crossing logic (direction, sign-of-cross-product)
  - Speed estimation (pixels/s → km/h)
  - Parking spot occupancy dwell-time detection
  - get_summary() returns correct aggregated data
  - reset_counts() clears per-interval data
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "edge-node"))

from analytics import AnalyticsEngine, CountingLine, ParkingSpot


# ── Counting line tests ────────────────────────────────────────────────────────

class TestCountingLineCrossing:
    """Test the _crosses_line static helper directly."""

    @pytest.fixture
    def engine(self, tmp_path):
        """Minimal engine with no spots or lines (we'll call helpers directly)."""
        import yaml
        cl_config = {"counting_lines": []}
        ps_config = {"occupied_threshold_seconds": 5, "parking_spots": []}
        (tmp_path / "cl.yaml").write_text(yaml.dump(cl_config))
        (tmp_path / "ps.yaml").write_text(yaml.dump(ps_config))
        return AnalyticsEngine.from_configs(
            counting_lines_path=str(tmp_path / "cl.yaml"),
            parking_spots_path=str(tmp_path / "ps.yaml"),
        )

    def test_upward_crossing_detected(self, engine):
        line = CountingLine("L", start=(0, 400), end=(1280, 400), direction="up")
        prev = (640, 450)   # below the line (y > 400)
        curr = (640, 350)   # above the line (y < 400)
        assert engine._crosses_line(prev, curr, line) is True

    def test_downward_crossing_not_counted_for_up(self, engine):
        line = CountingLine("L", start=(0, 400), end=(1280, 400), direction="up")
        prev = (640, 350)
        curr = (640, 450)   # moving down — should NOT count for "up" direction
        assert engine._crosses_line(prev, curr, line) is False

    def test_rightward_crossing_detected(self, engine):
        line = CountingLine("L", start=(640, 0), end=(640, 720), direction="right")
        prev = (600, 360)
        curr = (680, 360)
        assert engine._crosses_line(prev, curr, line) is True

    def test_leftward_crossing_not_counted_for_right(self, engine):
        line = CountingLine("L", start=(640, 0), end=(640, 720), direction="right")
        prev = (680, 360)
        curr = (600, 360)
        assert engine._crosses_line(prev, curr, line) is False

    def test_no_crossing_when_same_side(self, engine):
        line = CountingLine("L", start=(0, 400), end=(1280, 400), direction="up")
        prev = (640, 350)
        curr = (640, 300)   # both above line — no crossing
        assert engine._crosses_line(prev, curr, line) is False


# ── Speed estimation tests ─────────────────────────────────────────────────────

class TestSpeedEstimation:
    @pytest.fixture
    def engine(self, analytics_engine):
        return analytics_engine

    def test_stationary_object_zero_speed(self, engine):
        speed = engine._estimate_speed((100, 100), (100, 100))
        assert speed == 0.0

    def test_moving_object_positive_speed(self, engine):
        engine.set_pixels_per_metre(10.0)
        engine.fps = 30
        # Moving 30 pixels in 1 frame = 30/10 = 3 m/frame × 30 FPS = 90 m/s = 324 km/h
        # (unrealistic but mathematically correct)
        speed = engine._estimate_speed((0, 0), (30, 0))
        expected_kmh = (30 / 10.0) * 30 * 3.6
        assert abs(speed - expected_kmh) < 0.5

    def test_zero_pixels_per_metre_returns_zero(self, engine):
        engine.set_pixels_per_metre(0.0)
        speed = engine._estimate_speed((0, 0), (100, 0))
        assert speed == 0.0

    def test_speed_rounded_to_2dp(self, engine):
        engine.set_pixels_per_metre(7.0)
        speed = engine._estimate_speed((0, 0), (15, 0))
        assert speed == round(speed, 2)


# ── Parking occupancy tests ────────────────────────────────────────────────────

class TestParkingOccupancy:
    def test_spot_not_occupied_before_threshold(self, analytics_engine):
        """A vehicle centroid inside the spot but before dwell time should not mark it occupied."""
        engine = analytics_engine
        # Spot A1: roi = [100, 300, 200, 400] — centroid (150, 350) is inside

        class MockTrack:
            track_id = 1
            label = "car"
            centroid = (150, 350)

        engine._update_parking([MockTrack()], time.monotonic())
        assert engine.get_parking_state().get("A1") is False

    def test_spot_occupied_after_threshold(self, analytics_engine):
        """After dwell time, the spot should be marked occupied."""
        engine = analytics_engine
        engine.parking_threshold_seconds = 0.01  # 10ms threshold for fast testing

        class MockTrack:
            track_id = 1
            label = "car"
            centroid = (150, 350)

        # Simulate: first call sets occupied_since
        now = time.monotonic()
        engine._update_parking([MockTrack()], now)
        # Second call after threshold has passed
        time.sleep(0.05)
        engine._update_parking([MockTrack()], time.monotonic())
        assert engine.get_parking_state().get("A1") is True

    def test_pedestrian_does_not_occupy_spot(self, analytics_engine):
        """Pedestrians should not trigger parking occupancy."""
        engine = analytics_engine

        class MockTrack:
            track_id = 2
            label = "pedestrian"
            centroid = (150, 350)

        engine._update_parking([MockTrack()], time.monotonic())
        assert engine.get_parking_state().get("A1") is False

    def test_spot_freed_when_vehicle_leaves(self, analytics_engine):
        """Spot should be marked free when the vehicle departs."""
        engine = analytics_engine
        engine.parking_threshold_seconds = 0.01

        class MockTrack:
            track_id = 3
            label = "car"
            centroid = (150, 350)

        now = time.monotonic()
        engine._update_parking([MockTrack()], now)
        time.sleep(0.05)
        engine._update_parking([MockTrack()], time.monotonic())
        assert engine.get_parking_state()["A1"] is True

        # Vehicle leaves
        engine._update_parking([], time.monotonic())
        assert engine.get_parking_state()["A1"] is False


# ── Summary and reset ─────────────────────────────────────────────────────────

class TestSummaryAndReset:
    def test_summary_structure(self, analytics_engine):
        summary = analytics_engine.get_summary()
        assert hasattr(summary, "counts_by_class")
        assert hasattr(summary, "line_counts")
        assert hasattr(summary, "parking")
        assert hasattr(summary, "avg_speed_kmh")
        assert hasattr(summary, "timestamp")

    def test_reset_clears_counts(self, analytics_engine, tracker, sample_detection):
        """After reset, counts should be zero."""
        engine = analytics_engine
        # Process a frame
        from tracker import KalmanBoxTracker

        KalmanBoxTracker.count = 0
        tracks = tracker.update([sample_detection])
        engine.process(tracks, time.monotonic())

        engine.reset_counts()
        summary = engine.get_summary()
        assert sum(summary.counts_by_class.values()) == 0
        assert summary.avg_speed_kmh == 0.0
        assert all(count == 0 for count in summary.line_counts.values())
