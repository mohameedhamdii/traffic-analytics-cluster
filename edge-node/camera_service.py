import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np
import yaml



logger = logging.getLogger(__name__ )


@dataclass
class Frame:
    data :np.ndarray  # HxWxC BGR image
    timestamp: float

    frame_id :int
    width:int=field ( init= False)
    height: int = field ( init =False)

    def __post_init__(self):
        self.height ,self.width =self.data.shape [:2 ]




class CameraService :
    def __init__(
        self ,
        resolution :Tuple [ int ,int]= (1280,720 ) ,
        fps: int = 30,
        rotation: int = 0,
        yolo_input_size: Tuple[int, int] = (640, 640),
        device_index: int = 0,
        use_picamera2 :bool = True,
        queue_maxsize: int = 2,
    ):
        self.resolution= resolution
        self.fps= fps
        self.rotation= rotation
        self.yolo_input_size =yolo_input_size

        self.device_index= device_index
        self.use_picamera2 = use_picamera2

        # keep queue tiny so pipeline drops old frames instead of lagging behind
        self._frame_queue: queue.Queue[Frame] = queue.Queue(maxsize=queue_maxsize)
        self._stop_event =threading.Event ()
        self._thread: Optional[threading.Thread] = None
        self._frame_counter=0
        self._camera = None
        self._backend= "opencv"


    @ classmethod
    def from_config(cls, config_path: str) -> "CameraService":
        with open (config_path,"r")as f:
            cfg = yaml.safe_load(f)
        return cls(
            resolution= tuple (cfg.get( "resolution" ,[1280 , 720] )) ,
            fps=cfg.get ("fps",30) ,
            rotation=cfg.get("rotation", 0),
            yolo_input_size = tuple( cfg.get ( "yolo_input_size" ,[640 , 640])) ,
            device_index = cfg.get ("device_index" ,0) ,
            use_picamera2=cfg.get("use_picamera2", True),
        )

    def start(self ) -> None:
        self._stop_event.clear ()
        self._thread =threading.Thread(
            target=self._capture_loop, name="CameraCapture", daemon=True
        )
        self._thread.start()
        logger.info ("CameraService started (resolution=%s, fps=%d)", self.resolution , self.fps )

    def stop (self) -> None :
        self._stop_event.set ()
        if self._thread is not None :
            self._thread.join(timeout=5.0)
        if self._camera is not None:
            self._release_camera ( )
        logger.info("CameraService stopped.")

    def get_frame( self ,timeout: float=0.1)->Optional[ Frame]:
        try:
            return self._frame_queue.get ( timeout = timeout)
        except queue.Empty:
            # caller loops on none
            return None


    @ property
    def is_running( self) ->bool :
        return self._thread is not None and self._thread.is_alive()

    def _open_camera( self)-> bool :
        # try rpi cam3 first, fallback to usb webcams
        if self.use_picamera2:
            try:
                from picamera2 import Picamera2

                self._camera=Picamera2( )
                config = self._camera.create_video_configuration(
                    main= {"size" : self.resolution , "format" : "BGR888"},
                    controls= { "FrameRate" :float( self.fps) } ,
                )
                self._camera.configure (config)
                self._camera.start()
                self._backend="picamera2"
                logger.info("Camera opened via picamera2.")
                return True
            except Exception as e:
                # picamera2 not installed on non-rpi dev machines
                logger.warning("picamera2 unavailable (%s), falling back to OpenCV.", e)



        cap = cv2.VideoCapture(self.device_index)
        if not cap.isOpened():
            logger.error("Cannot open camera at device index %d.", self.device_index)
            return False
        cap.set( cv2.CAP_PROP_FRAME_WIDTH ,self.resolution[ 0 ] )
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.resolution[ 1 ] )
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        #cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        #buffersize=1 keeps latency minimal
        cap.set(cv2.CAP_PROP_BUFFERSIZE,1 )
        self._camera = cap
        self._backend = "opencv"
        logger.info("Camera opened via OpenCV (device=%d).", self.device_index)
        return True

    def _release_camera ( self) -> None :
        try:
            if self._backend == "picamera2" :
                self._camera.stop()
                self._camera.close()
            else:
                self._camera.release()
        except Exception as e:
            # cleanup error shouldn't crash app exit
            logger.warning ("Error releasing camera: %s", e )

        self._camera= None

    def _read_frame(self ) ->Optional [ np.ndarray ]:
        if self._backend== "picamera2":
            raw = self._camera.capture_array()
            return cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
        else:
            ret ,frame = self._camera.read()
            return frame if ret else None

    def _preprocess ( self, frame: np.ndarray )->np.ndarray:

        if self.rotation == 90:
            frame = cv2.rotate (frame , cv2.ROTATE_90_CLOCKWISE)
        elif self.rotation == 180:
            frame=cv2.rotate ( frame,cv2.ROTATE_180)
        elif self.rotation== 270 :
            frame=cv2.rotate ( frame,cv2.ROTATE_90_COUNTERCLOCKWISE )
        return frame

    def _capture_loop(self )->None :
        if not self._open_camera ( ):
            logger.error("Failed to open camera. Capture thread exiting.")
            return

        frame_interval = 1.0 /self.fps
        next_frame_time =time.monotonic( )


        while not self._stop_event.is_set( ) :
            now=time.monotonic( )
            if now<next_frame_time:
                time.sleep(next_frame_time-now )

            raw =self._read_frame()
            if raw is None:
                # frame read failed, wait a bit and retry
                logger.warning("Failed to read frame, retrying.")
                time.sleep ( 0.05 )
                continue

            raw=self._preprocess( raw )


            ts = time.monotonic()

            self._frame_counter += 1
            frame = Frame(data= raw, timestamp=ts, frame_id=self._frame_counter)

            # drop oldest frame if consumer is lagging behind
            if self._frame_queue.full ():
                try:
                    self._frame_queue.get_nowait()
                except queue.Empty:


                    pass
            try :
                self._frame_queue.put_nowait(frame)
            except queue.Full :
                pass  # queue full race condition

            next_frame_time +=frame_interval

        self._release_camera ( )
