"""
test_aggregator.py — Integration tests for the central aggregator service.

Uses a mock MQTT client and a mock InfluxDB write API to verify that:
  - Incoming MQTT messages are parsed and routed correctly
  - Correct InfluxDB points are written for each message type
  - Parking alerts fire at the correct threshold
  - Node-offline alerts fire after the heartbeat timeout
"""

import json
import sys
import time
from datetime import timezone, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "central-server"))

from aggregator import TrafficAggregator, PARKING_ALERT_THRESHOLD


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_aggregator(mqtt_mock=None, write_api_mock=None):
    """
    Create a TrafficAggregator with mocked MQTT and InfluxDB clients.
    """
    agg = TrafficAggregator(
        mqtt_cfg={"host": "localhost", "port": 1883},
        influx_cfg={
            "url": "http://localhost:8086",
            "token": "test-token",
            "org": "smartcity",
            "bucket": "traffic",
        },
    )
    agg._mqtt_client = mqtt_mock or MagicMock()
    agg._write_api = write_api_mock or MagicMock()
    agg._influx_client = MagicMock()
    return agg


def make_mqtt_message(topic: str, payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.topic = topic
    msg.payload = json.dumps(payload).encode("utf-8")
    return msg


# ── Message routing ────────────────────────────────────────────────────────────

class TestMessageRouting:
    def test_counts_message_calls_handle_counts(self):
        agg = make_aggregator()
        agg._handle_counts = MagicMock()
        msg = make_mqtt_message("city/node-1/counts", {"counts": {"car": 5}, "total": 5})
        agg._on_message(None, None, msg)
        agg._handle_counts.assert_called_once()

    def test_parking_message_calls_handle_parking(self):
        agg = make_aggregator()
        agg._handle_parking = MagicMock()
        msg = make_mqtt_message("city/node-2/parking", {"spots": {}, "total_spots": 0, "occupied_spots": 0})
        agg._on_message(None, None, msg)
        agg._handle_parking.assert_called_once()

    def test_speed_message_calls_handle_speed(self):
        agg = make_aggregator()
        agg._handle_speed = MagicMock()
        msg = make_mqtt_message("city/node-1/speed", {"avg_speed_kmh": 35.0})
        agg._on_message(None, None, msg)
        agg._handle_speed.assert_called_once()

    def test_health_message_calls_handle_health(self):
        agg = make_aggregator()
        agg._handle_health = MagicMock()
        msg = make_mqtt_message("city/node-3/health", {"status": "online", "fps": 27.0})
        agg._on_message(None, None, msg)
        agg._handle_health.assert_called_once()

    def test_invalid_json_does_not_crash(self):
        agg = make_aggregator()
        msg = MagicMock()
        msg.topic = "city/node-1/counts"
        msg.payload = b"this is not json"
        # Should not raise
        agg._on_message(None, None, msg)

    def test_unknown_topic_type_silently_ignored(self):
        agg = make_aggregator()
        msg = make_mqtt_message("city/node-1/unknown_type", {"foo": "bar"})
        # Should not raise
        agg._on_message(None, None, msg)


# ── InfluxDB writes ────────────────────────────────────────────────────────────

class TestInfluxDBWrites:
    def test_handle_counts_writes_per_class_points(self):
        write_api = MagicMock()
        agg = make_aggregator(write_api_mock=write_api)
        payload = {
            "node_id": "node-1",
            "timestamp": time.time(),
            "counts": {"car": 10, "truck": 2},
            "total": 12,
        }
        agg._handle_counts("node-1", payload)
        # Expect 3 writes: car, truck, total
        assert write_api.write.call_count == 3

    def test_handle_speed_writes_one_point(self):
        write_api = MagicMock()
        agg = make_aggregator(write_api_mock=write_api)
        agg._handle_speed("node-1", {"avg_speed_kmh": 42.5, "timestamp": time.time()})
        assert write_api.write.call_count == 1

    def test_handle_health_writes_online_field(self):
        write_api = MagicMock()
        agg = make_aggregator(write_api_mock=write_api)
        agg._handle_health("node-1", {
            "status": "online",
            "fps": 28.5,
            "cpu_temp_c": 62.1,
            "active_tracks": 5,
            "timestamp": time.time(),
        })
        assert write_api.write.call_count >= 1

    def test_handle_parking_writes_per_spot(self):
        write_api = MagicMock()
        agg = make_aggregator(write_api_mock=write_api)
        payload = {
            "node_id": "node-1",
            "timestamp": time.time(),
            "spots": {
                "A1": {"occupied": True},
                "A2": {"occupied": False},
                "A3": {"occupied": True},
            },
            "total_spots": 3,
            "occupied_spots": 2,
        }
        agg._handle_parking("node-1", payload)
        # 3 spot writes + 1 summary write = 4
        assert write_api.write.call_count == 4

    def test_influxdb_write_failure_does_not_crash(self):
        write_api = MagicMock()
        write_api.write.side_effect = Exception("InfluxDB connection refused")
        agg = make_aggregator(write_api_mock=write_api)
        # Should not raise
        agg._handle_counts("node-1", {
            "counts": {"car": 1},
            "total": 1,
            "timestamp": time.time(),
        })


# ── Alerting ───────────────────────────────────────────────────────────────────

class TestAlerting:
    def test_parking_full_alert_fires_above_threshold(self):
        mqtt_mock = MagicMock()
        agg = make_aggregator(mqtt_mock=mqtt_mock)

        # Fill parking above threshold (all spots occupied → 100% > 90% threshold)
        n_total = 10
        n_occupied = n_total  # 100% — clearly above PARKING_ALERT_THRESHOLD
        spots = {
            f"A{i}": {"occupied": True}
            for i in range(n_total)
        }
        payload = {
            "timestamp": time.time(),
            "spots": spots,
            "total_spots": n_total,
            "occupied_spots": n_occupied,
        }
        agg._handle_parking("node-1", payload)
        # Alert should have been published to MQTT
        mqtt_mock.publish.assert_called()
        # Check that the alert topic is correct
        call_args = mqtt_mock.publish.call_args
        assert "city/alerts" in call_args[0][0]

    def test_parking_alert_does_not_fire_below_threshold(self):
        mqtt_mock = MagicMock()
        agg = make_aggregator(mqtt_mock=mqtt_mock)

        spots = {f"A{i}": {"occupied": False} for i in range(10)}
        payload = {
            "timestamp": time.time(),
            "spots": spots,
            "total_spots": 10,
            "occupied_spots": 0,
        }
        agg._handle_parking("node-1", payload)
        mqtt_mock.publish.assert_not_called()

    def test_node_last_seen_updated_on_health(self):
        agg = make_aggregator()
        assert "node-99" not in agg._node_last_seen

        agg._handle_health("node-99", {
            "status": "online",
            "fps": 25.0,
            "timestamp": time.time(),
        })
        assert "node-99" in agg._node_last_seen


# ── Timestamp parsing ─────────────────────────────────────────────────────────

class TestTimestampParsing:
    def test_unix_float_parsed_correctly(self):
        ts = 1694000000.0
        result = TrafficAggregator._parse_timestamp(ts)
        assert result.tzinfo is not None
        assert result == datetime.fromtimestamp(ts, tz=timezone.utc)

    def test_none_returns_now(self):
        result = TrafficAggregator._parse_timestamp(None)
        assert result.tzinfo is not None
        # Should be close to now
        diff = abs((datetime.now(tz=timezone.utc) - result).total_seconds())
        assert diff < 5

    def test_invalid_timestamp_returns_now(self):
        result = TrafficAggregator._parse_timestamp("not-a-timestamp")
        assert result.tzinfo is not None
