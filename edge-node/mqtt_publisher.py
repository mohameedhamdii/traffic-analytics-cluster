import json
import logging
import os
import platform
import threading
import time
from typing import Any, Dict, List, Optional

import paho.mqtt.client as mqtt
import yaml



logger = logging.getLogger(__name__)


class MQTTPublisher:
    RECONNECT_DELAY_MIN= 1
    RECONNECT_DELAY_MAX=60


    def __init__(
        self,
        broker_host : str ,
        broker_port: int ,
        node_id:str ,
        client_id: Optional[str] = None,
        publish_raw_tracks: bool= True ,
        raw_track_interval:int = 5 ,
        keepalive: int = 60,
    ) :
        self.broker_host= broker_host
        self.broker_port = broker_port
        self.node_id = node_id
        self.publish_raw_tracks = publish_raw_tracks


        self.raw_track_interval = raw_track_interval
        self.keepalive=keepalive

        self._client_id = client_id or f"{node_id}-{platform.node()}"
        self._client: Optional[mqtt.Client] = None
        self._connected=threading.Event ( )
        self._lock =threading.Lock()

        self._reconnect_delay=self.RECONNECT_DELAY_MIN
        self._frame_counter=0
        self._stop_event= threading.Event ()
        self._heartbeat_thread: Optional[threading.Thread] = None

    @classmethod
    def from_config( cls ,config_path: str ,node_id :Optional [str] =None)->"MQTTPublisher" :
        with open(config_path, "r") as f:
            cfg = yaml.safe_load( f )
        effective_node_id = (
            node_id
            or os.environ.get ( "NODE_ID")
            or cfg.get( "client_id" ,"node-1")
        )

        return cls(
            broker_host= os.environ.get ( "MQTT_BROKER_HOST" )or cfg.get ("broker_host", "localhost"),
            broker_port=int(os.environ.get("MQTT_BROKER_PORT") or cfg.get("broker_port", 1883)),
            node_id = effective_node_id ,
            client_id= effective_node_id,
            publish_raw_tracks =cfg.get( "publish_raw_tracks",True ) ,
            raw_track_interval=cfg.get("raw_track_interval", 5),
        )

    def connect ( self ,timeout: float = 15.0 )-> bool:
        self._client = mqtt.Client( client_id =self._client_id, protocol=mqtt.MQTTv5)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect=self._on_disconnect
        self._client.on_publish= self._on_publish

        # lwt payload so subscribers know node went offline
        will_payload = json.dumps ( {
            "node_id": self.node_id,
            "status": "offline",
            "timestamp": time.time( ),
        } )
        self._client.will_set(
            topic =self._topic ("health"),
            payload=will_payload,
            qos =1,
            retain = True ,
        )

        try:
            self._client.connect(
                self.broker_host, self.broker_port, keepalive=self.keepalive
            )
        except Exception as e:
            logger.error("MQTT connect failed: %s", e)
            return False

        self._client.loop_start()

        if not self._connected.wait(timeout = timeout ):
            logger.error( "Timed out waiting for MQTT connection.")
            return False

        self._start_heartbeat ( )
        logger.info (
            "MQTTPublisher connected to %s:%d as '%s'" ,
            self.broker_host ,
            self.broker_port ,
            self._client_id,
        )
        return True

    def disconnect(self ) -> None:

        self._stop_event.set( )
        if self._heartbeat_thread:
            self._heartbeat_thread.join ( timeout =5.0)
        if self._client:
            #broadcast clean shutdown offline status
            self._publish_direct(
                self._topic("health"),
                {"node_id": self.node_id, "status": "offline", "timestamp": time.time()},
                qos=1,
                retain=True,
            )
            self._client.loop_stop()
            self._client.disconnect()
        logger.info("MQTTPublisher disconnected.")

    def publish_tracks(
        self,
        frame_id: int,
        timestamp: float,
        tracks :List [ Dict[str , Any ]],
    ) -> None:
        if not self.publish_raw_tracks:
            return
        self._frame_counter +=1
        # downsample tracks broadcast so we dont choke wifi
        if self._frame_counter % self.raw_track_interval !=0:
            return



        payload = {
            "node_id": self.node_id,
            "timestamp": timestamp,
            "frame_id":frame_id ,
            "tracks" : tracks,
        }
        self._publish_direct ( self._topic ( "tracks" ) ,payload,qos= 0 )

    def publish_counts(self , counts_by_class: Dict[str , int] ) -> None:
        payload = {
            "node_id": self.node_id,
            "timestamp": time.time(),
            "counts":counts_by_class,
            "total" :sum (counts_by_class.values ( )),
        }
        self._publish_direct(self._topic("counts"), payload, qos=1)

    def publish_parking ( self, parking_state: Dict [str,bool ] ) ->None :
        # retain=True so new dashboard subscribers immediately see current lot status
        payload = {
            "node_id":self.node_id,
            "timestamp" : time.time(),
            "spots": {
                spot_id :{"occupied" : occupied}
                for spot_id, occupied in parking_state.items()
            },
            "total_spots" :len( parking_state ) ,
            "occupied_spots": sum ( 1 for v in parking_state.values( )if v) ,
        }
        self._publish_direct( self._topic ("parking"), payload,qos=1,retain= True )

    def publish_speed(self,avg_speed_kmh :float ) -> None:
        payload={
            "node_id" : self.node_id ,
            "timestamp" : time.time (),
            "avg_speed_kmh": avg_speed_kmh,
        }
        self._publish_direct(self._topic("speed"), payload, qos=1)

    def publish_health (
        self,
        fps: float,
        cpu_temp:Optional [float]= None ,
        hailo_util :Optional[float ]=None,
        active_tracks : int =0 ,
    ) -> None:
        cpu_temp = cpu_temp or self._read_cpu_temp()
        payload = {
            "node_id":self.node_id ,
            "status" :"online" ,
            "timestamp" :time.time ( ) ,
            "fps": round ( fps,2) ,
            "cpu_temp_c":round ( cpu_temp, 1 )if cpu_temp else None,
            "hailo_util_pct" : round( hailo_util ,1 )if hailo_util else None ,
            "active_tracks": active_tracks ,
            "hostname":platform.node () ,
        }
        self._publish_direct( self._topic ("health" ), payload ,qos= 1,retain = True )

    def _topic (self , suffix :str)->str :
        return f"city/{self.node_id}/{suffix}"

    def _publish_direct(
        self ,
        topic :str,
        payload: Dict[str, Any],
        qos: int = 0,
        retain: bool = False,
    )->None :
        if not self._connected.is_set ( ):

            logger.debug("MQTT not connected, skipping publish to %s", topic)
            return
        try:
            msg_info= self._client.publish (
                topic, json.dumps(payload), qos=qos, retain=retain
            )
            if msg_info.rc != mqtt.MQTT_ERR_SUCCESS :
                logger.warning ("MQTT publish to '%s' failed: rc=%d",topic, msg_info.rc )
        except Exception as e :
            # catch socket error if broker disconnected mid-flight
            logger.warning ( "MQTT publish exception: %s" , e)

    def _start_heartbeat ( self ) ->None :

        self._heartbeat_thread =threading.Thread (
            target=self._heartbeat_loop, name = "MQTTHeartbeat",daemon= True
        )
        self._heartbeat_thread.start( )

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set( ):
            self.publish_health( fps = 0.0 )
            self._stop_event.wait(timeout=10.0)

    @staticmethod
    def _read_cpu_temp ( ) ->Optional [float ]:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
                return int( f.read ( ).strip ( ) )/1000.0
        except Exception :
            #not on linux or permission denied
            return None


    def _on_connect( self,client,userdata ,flags,rc ,properties = None ) ->None :
        if rc == 0 :
            self._connected.set()
            self._reconnect_delay =self.RECONNECT_DELAY_MIN
            logger.info ( "MQTT connected (rc=0).")
        else:
            logger.error("MQTT connection failed with rc=%d", rc)

    def _on_disconnect(self ,client ,userdata,rc ,properties =None )-> None:
        self._connected.clear( )
        if rc != 0:
            logger.warning ( "Unexpected MQTT disconnect (rc=%d). Reconnecting..." , rc)



    def _on_publish(self ,client, userdata,mid ) -> None :

        logger.debug ( "MQTT message %d published." ,mid)
