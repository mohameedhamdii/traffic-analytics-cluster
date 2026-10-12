import json
import logging
import os
import signal
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone


from typing import Any, Dict, Optional

import paho.mqtt.client as mqtt
import yaml
from influxdb_client import InfluxDBClient,Point , WritePrecision

from influxdb_client.client.write_api import SYNCHRONOUS

logging.basicConfig (
    level = logging.INFO,
    format= "%(asctime)s [%(levelname)s] %(name)s: %(message)s" ,
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers = [logging.StreamHandler (sys.stdout ) ],
)
logger = logging.getLogger("aggregator" )

NODE_OFFLINE_TIMEOUT_SECONDS=30  #node considered dead after 30s no ping
PARKING_ALERT_THRESHOLD = 0.90     # 90% occupancy threshold


ALERT_TOPIC="city/alerts"


def load_influxdb_config (path: str = "config/influxdb.yaml" )-> Dict[ str, Any] :
    cfg: Dict[str, Any] = {}
    if os.path.exists( path):
        with open( path,"r" ) as f :
            cfg = yaml.safe_load(f) or {}

    return {
        "url": os.environ.get("INFLUXDB_URL") or cfg.get("url", "http://localhost:8086"),
        "token": os.environ.get("INFLUXDB_TOKEN") or cfg.get("token", ""),
        "org":os.environ.get("INFLUXDB_ORG")or cfg.get ( "org" ,"smartcity") ,
        "bucket": os.environ.get("INFLUXDB_BUCKET") or cfg.get("bucket", "traffic"),
    }


def load_mqtt_config(path: str = "config/mqtt.yaml") -> Dict[str, Any]:
    cfg: Dict [ str , Any ]={}
    if os.path.exists ( path ):
        with open( path, "r" ) as f:
            cfg=yaml.safe_load(f)or {}
    return {
        "host": os.environ.get("MQTT_BROKER_HOST") or cfg.get("broker_host", "localhost"),
        "port": int(os.environ.get("MQTT_BROKER_PORT") or cfg.get("broker_port", 1883)),
    }


class TrafficAggregator :

    def __init__(self, mqtt_cfg: Dict, influx_cfg: Dict):
        self._mqtt_cfg=mqtt_cfg
        self._influx_cfg = influx_cfg

        self._influx_client : Optional [InfluxDBClient]= None
        self._write_api= None
        self._mqtt_client: Optional[mqtt.Client] = None

        self._node_last_seen : Dict [str,float] = { }
        self._node_parking: Dict[str, Dict[str, bool]] = defaultdict(dict)

        self._stop_event= threading.Event ( )
        self._alert_thread : Optional [threading.Thread]=None

    def start ( self)-> None :
        self._connect_influxdb()
        self._connect_mqtt( )
        self._start_alert_monitor()
        logger.info("Aggregator started. Waiting for messages...")

    def run_forever ( self) -> None :
        self.start()
        try :
            while not self._stop_event.is_set():
                self._stop_event.wait(timeout=1.0)
        finally:

            self.stop()

    def stop( self) ->None :
        logger.info ("Aggregator stopping...")
        self._stop_event.set()
        if self._alert_thread :
            self._alert_thread.join (timeout =5.0 )
        if self._mqtt_client:
            self._mqtt_client.loop_stop ()
            self._mqtt_client.disconnect()
        if self._influx_client:
            self._write_api.close()
            self._influx_client.close( )
        logger.info ( "Aggregator stopped.")

    def _connect_influxdb ( self)->None :


        url =self._influx_cfg["url"]
        logger.info ( "Connecting to InfluxDB at %s...",url )
        self._influx_client =InfluxDBClient(
            url =url,
            token =self._influx_cfg[ "token" ] ,
            org= self._influx_cfg [ "org"] ,
        )
        self._write_api=self._influx_client.write_api (write_options = SYNCHRONOUS)

        for attempt in range(1 ,11):
            try:
                health=self._influx_client.health( )
                if health.status == "pass":
                    logger.info("InfluxDB healthy.")
                    return
            except Exception as e:
                # influx takes a few secs on fresh compose up
                logger.warning("InfluxDB not ready (attempt %d): %s", attempt, e)
            time.sleep(3)

        logger.error("InfluxDB did not become healthy after 10 attempts.")

    def _write_point(self,point :Point ) -> None:
        try:
            self._write_api.write(
                bucket= self._influx_cfg [ "bucket" ] ,
                org=self._influx_cfg["org"],
                record=point,
            )
        except Exception as e:
            #log and continue so one bad point doesnt kill the consumer
            logger.warning ("InfluxDB write failed: %s",e )

    def _connect_mqtt(self) -> None:
        host= self._mqtt_cfg["host"]

        port =self._mqtt_cfg["port" ]
        logger.info ( "Connecting to MQTT broker at %s:%d...",host, port)

        self._mqtt_client = mqtt.Client(
            client_id="aggregator-central", protocol=mqtt.MQTTv5
        )
        self._mqtt_client.on_connect = self._on_connect
        self._mqtt_client.on_message=self._on_message
        self._mqtt_client.on_disconnect = self._on_disconnect

        for attempt in range (1 , 21 ):
            try:

                self._mqtt_client.connect(host, port, keepalive=60)
                self._mqtt_client.loop_start ( )
                logger.info("MQTT connected.")
                return
            except Exception as e:
                # retry if mosquitto is still starting
                logger.warning( "MQTT connect attempt %d failed: %s" , attempt, e )
                time.sleep(3)

        logger.error ("Could not connect to MQTT broker after 20 attempts.")

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc== 0:
            logger.info( "MQTT on_connect OK, subscribing to topics." )
            topics= [
                ( "city/+/counts",1 ) ,
                ("city/+/parking" , 1) ,
                ("city/+/speed", 1),
                ("city/+/health" ,1 ) ,
                ("city/+/tracks", 0) ,# qos 0 ok for raw high-freq tracks
            ]

            self._mqtt_client.subscribe (topics )
        else:
            logger.error ( "MQTT on_connect error rc=%d",rc )

    def _on_disconnect(self , client , userdata, rc , properties=None)-> None :
        if rc !=0:
            logger.warning("MQTT disconnected unexpectedly (rc=%d)." , rc )

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        topic=msg.topic
        try:
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as e :
            #garbled payload or non-json message recieved
            logger.warning ( "Could not decode message on '%s': %s" , topic ,e)


            return


        parts=topic.split ( "/" )
        if len(parts) < 3:
            return

        node_id = parts[1]
        msg_type= parts [ 2 ]



        handlers= {
            "counts" : self._handle_counts ,
            "parking": self._handle_parking ,
            "speed": self._handle_speed,
            "health":self._handle_health ,
            "tracks" : self._handle_tracks,
        }
        handler=handlers.get (msg_type )
        if handler:
            try:
                handler( node_id ,payload)
            except Exception as e:
                logger.exception ("Error handling '%s' message: %s", msg_type , e )

    def _handle_counts( self , node_id: str, payload:Dict) -> None :
        ts = self._parse_timestamp(payload.get("timestamp"))

        counts: Dict[str, int] = payload.get("counts", {})

        for class_name, count in counts.items():
            point = (
                Point ("vehicle_count")
                .tag("node_id", node_id)
                .tag ("class", class_name)
                .field("count", int(count) )
                .time(ts, WritePrecision.S)
            )
            self._write_point( point )

        # total across all classes
        total= payload.get ( "total" , sum( counts.values ()))
        point= (
            Point( "vehicle_count")
            .tag("node_id" ,node_id )
            .tag( "class", "total")
            .field( "count" , int ( total))
            .time( ts,WritePrecision.S )
        )
        self._write_point(point )
        #logger.debug("wrote total count: %d", total)
        logger.debug ( "counts from %s: %s (total=%d)" , node_id , counts,total )

    def _handle_parking(self , node_id :str , payload : Dict)->None:


        ts = self._parse_timestamp(payload.get("timestamp"))
        spots:Dict [str ,Dict ] =payload.get("spots" ,{} )

        self._node_parking [ node_id ]= {
            spot_id: info.get ( "occupied" , False)
            for spot_id, info in spots.items()
        }

        for spot_id,info in spots.items() :
            occupied=int (info.get ("occupied", False ))
            point =(
                Point ("parking_occupancy")
                .tag("node_id", node_id)
                .tag( "spot_id" ,spot_id )
                .field ( "occupied",occupied)
                .time ( ts,WritePrecision.S )
            )
            self._write_point(point)

        total=payload.get("total_spots", len(spots))
        occupied_count = payload.get( "occupied_spots",sum (
            1 for info in spots.values() if info.get("occupied", False)
        ) )
        if total > 0:
            occupancy_rate=occupied_count / total
            point =(
                Point ("parking_summary" )
                .tag( "node_id" , node_id)
                .field("total_spots" ,total )
                .field ("occupied_spots" , occupied_count)
                .field ("occupancy_rate" ,round( occupancy_rate, 4))
                .time(ts, WritePrecision.S)
            )
            self._write_point(point)

            if occupancy_rate> PARKING_ALERT_THRESHOLD:
                #warn when lot is near capacity
                self._publish_alert (
                    alert_type="parking_full",
                    node_id =node_id,
                    message = f"Parking lot on {node_id} is {occupancy_rate:.0%} full "
                            f"({occupied_count}/{total} spots occupied).",
                )

        logger.debug (
            "parking from %s: %d/%d occupied" ,node_id , occupied_count ,total
        )

    def _handle_speed ( self ,node_id: str ,payload:Dict)-> None:

        ts=self._parse_timestamp(payload.get("timestamp" ))
        speed =payload.get( "avg_speed_kmh" , 0.0)
        point= (
            Point("avg_speed")
            .tag("node_id", node_id)
            .field( "speed_kmh" ,float (speed ))
            .time ( ts , WritePrecision.S )
        )
        self._write_point( point )
        logger.debug("speed from %s: %.1f km/h", node_id, speed)

    def _handle_health(self, node_id: str, payload: Dict) -> None:
        ts = self._parse_timestamp(payload.get("timestamp"))
        self._node_last_seen [node_id ]= time.monotonic ()

        status = payload.get("status", "online")
        online = 1 if status =="online" else 0

        fps= float( payload.get ("fps" , 0.0 ))

        cpu_temp = payload.get("cpu_temp_c")
        hailo_util = payload.get("hailo_util_pct")
        active_tracks= int( payload.get("active_tracks" ,0))

        point = Point ( "node_health" ).tag ( "node_id" ,node_id)
        point=point.field ("online" ,online )
        point =point.field( "fps" ,fps)
        point = point.field("active_tracks", active_tracks)
        if cpu_temp is not None:
            point = point.field("cpu_temp_c", float(cpu_temp))
        if hailo_util is not None:
            point= point.field ( "hailo_util_pct" , float( hailo_util ) )
        point = point.time( ts,WritePrecision.S)
        self._write_point ( point )

    def _handle_tracks(self, node_id: str, payload: Dict) -> None:
        #raw tracks are high frequency, skip writing to db
        #pass
        return

    def _start_alert_monitor (self) ->None:
        self._alert_thread = threading.Thread(
            target = self._alert_loop,name= "AlertMonitor", daemon= True
        )
        self._alert_thread.start()

    def _alert_loop( self)->None :
        while not self._stop_event.is_set( ) :
            now = time.monotonic ( )

            for node_id ,last_seen in list (self._node_last_seen.items () ):
                if now - last_seen > NODE_OFFLINE_TIMEOUT_SECONDS:
                    self._publish_alert(
                        alert_type = "node_offline" ,
                        node_id =node_id ,
                        message =f"Node {node_id} has not sent a heartbeat for "
                                f"{NODE_OFFLINE_TIMEOUT_SECONDS}s: it may be offline." ,
                    )
            self._stop_event.wait(timeout=10.0)

    def _publish_alert (
        self, alert_type :str,node_id : str,message:str
    ) ->None :
        alert= {
            "type" :alert_type ,
            "node_id": node_id,
            "message": message,
            "timestamp": time.time(),
        }
        logger.warning ("ALERT [%s] %s: %s", alert_type ,node_id,message)
        if self._mqtt_client:
            try:
                self._mqtt_client.publish (
                    ALERT_TOPIC, json.dumps(alert), qos=1, retain=False
                )
            except Exception as e:
                #mqtt write failed, nothing we can do here
                logger.warning("Failed to publish alert: %s" ,e )

    @staticmethod
    def _parse_timestamp(ts :Any ) -> datetime :
        if ts is None :
            return datetime.now( tz=timezone.utc)
        try:
            return datetime.fromtimestamp (float( ts) ,tz =timezone.utc )
        except(ValueError ,TypeError):


            # fallback if timestamp paramter is bad
            return datetime.now(tz=timezone.utc)


def main():
    logger.info ( "Starting Traffic Aggregator..." )

    mqtt_cfg = load_mqtt_config("config/mqtt.yaml" )
    influx_cfg=load_influxdb_config("config/influxdb.yaml" )

    aggregator=TrafficAggregator (mqtt_cfg = mqtt_cfg,influx_cfg =influx_cfg )

    def _shutdown(signum, frame):
        logger.info( "Shutdown signal received." )
        aggregator._stop_event.set ( )

    signal.signal ( signal.SIGTERM,_shutdown )
    signal.signal (signal.SIGINT ,_shutdown )

    aggregator.run_forever()


if __name__== "__main__":
    main( )
