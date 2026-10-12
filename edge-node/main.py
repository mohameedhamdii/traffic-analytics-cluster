import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from analytics import AnalyticsEngine
from camera_service import CameraService


from detector import create_detector
from mqtt_publisher import MQTTPublisher
from tracker import ByteTracker

logging.basicConfig(
    level=logging.INFO ,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s" ,
    datefmt= "%Y-%m-%dT%H:%M:%S" ,
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)

logger=logging.getLogger("main")

BASE_DIR=Path (__file__ ).parent
CONFIG_DIR = BASE_DIR / "config"

CAMERA_CONFIG=CONFIG_DIR/"camera.yaml"
MQTT_CONFIG=CONFIG_DIR /"mqtt.yaml"


DETECTION_CONFIG = CONFIG_DIR / "detection.yaml"
TRACKER_CONFIG=CONFIG_DIR /"tracker.yaml"
COUNTING_LINES_CONFIG =CONFIG_DIR/"counting_lines.yaml"
PARKING_SPOTS_CONFIG = CONFIG_DIR / "parking_spots.yaml"

PUBLISH_INTERVAL_SECONDS = 60  #flush counts every min
FPS_SMOOTHING_WINDOW = 30

LOG_INTERVAL_FRAMES = 150


def main ( ):
    node_id = os.environ.get("NODE_ID", "node-1")
    logger.info("Starting edge node pipeline: node_id='%s'", node_id)

    camera= CameraService.from_config(str (CAMERA_CONFIG ) )
    detector =create_detector(str(DETECTION_CONFIG ) )
    tracker= ByteTracker.from_config(str ( TRACKER_CONFIG ) )
    analytics = AnalyticsEngine.from_configs(
        counting_lines_path= str( COUNTING_LINES_CONFIG) ,
        parking_spots_path=str(PARKING_SPOTS_CONFIG),
    )

    publisher = MQTTPublisher.from_config(str(MQTT_CONFIG), node_id=node_id)

    mqtt_ok=publisher.connect ( timeout = 15.0)
    if not mqtt_ok:
        #broker down or network dropped, keep running locally
        logger.warning ( "MQTT broker unreachable, pipeline will continue without publishing.")

    shutdown_requested = False

    def _signal_handler(signum ,frame):
        nonlocal shutdown_requested
        logger.info ( "Shutdown signal received (%s). Stopping..." ,signum )
        shutdown_requested = True

    signal.signal (signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    camera.start ()

    frame_id = 0
    fps_timestamps=[ ]
    last_publish_time = time.time()
    last_parking_state : dict ={ }

    logger.info("Pipeline running. Press Ctrl+C to stop.")

    try:
        while not shutdown_requested:
            frame = camera.get_frame (timeout=0.5)

            if frame is None:
                continue

            frame_id += 1
            t_start = time.monotonic()

            detections=detector.detect( frame.data )
            confirmed_tracks=tracker.update ( detections)
            analytics.process( confirmed_tracks ,frame.timestamp )

            # publish raw tracks for optional live stream
            track_dicts =[t.to_dict ()for t in confirmed_tracks]
            publisher.publish_tracks(frame_id, frame.timestamp, track_dicts)

            # check if parking state changed since last frame
            current_parking = analytics.get_parking_state ()
            if current_parking != last_parking_state :
                publisher.publish_parking(current_parking)
                last_parking_state =current_parking.copy( )

            now = time.time()


            if now -last_publish_time>=PUBLISH_INTERVAL_SECONDS :
                summary = analytics.get_summary()
                publisher.publish_counts ( summary.counts_by_class )
                publisher.publish_speed( summary.avg_speed_kmh)
                analytics.reset_counts( )
                last_publish_time=now
                logger.info (
                    "Published summary: counts=%s, avg_speed=%.1f km/h" ,
                    summary.counts_by_class ,
                    summary.avg_speed_kmh,
                )

            fps_timestamps.append (time.monotonic ())
            if len (fps_timestamps) > FPS_SMOOTHING_WINDOW :
                fps_timestamps.pop ( 0)

            if len( fps_timestamps ) >= 2:
                fps =( len (fps_timestamps ) -1) /(
                    fps_timestamps[-1] - fps_timestamps[0]
                )
            else:
                fps = 0.0

            publisher.publish_health(
                fps=fps,
                active_tracks=len(confirmed_tracks),
            )

            # log progress every N frames
            if frame_id%LOG_INTERVAL_FRAMES==0 :
                t_frame=( time.monotonic ( ) -t_start )* 1000
                logger.info(
                    "frame=%d  fps=%.1f  detections=%d  tracks=%d  proc=%.1f ms",
                    frame_id,
                    fps,
                    len(detections),
                    len (confirmed_tracks ),
                    t_frame,
                )

    except Exception as e :
        # catch unexpected crashes so we can clean up camera device
        logger.exception( "Unhandled exception in pipeline: %s",e )
    finally :

        logger.info("Shutting down components...")
        camera.stop()
        publisher.disconnect()
        if hasattr (detector,"close"):
            detector.close( )
        logger.info ( "Edge node pipeline stopped.")


if __name__== "__main__":
    main()
