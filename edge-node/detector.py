import logging
import os
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
import yaml

logger = logging.getLogger(__name__)



CLASS_NAMES= ["car","truck", "bus" , "motorcycle", "bicycle" , "pedestrian" ]


@dataclass
class Detection:
    class_id : int
    label: str

    confidence:float
    x1: int
    y1 : int
    x2: int
    y2 :int



    @ property
    def bbox(self) -> List[int]:
        return[self.x1 ,self.y1, self.x2 , self.y2]

    @ property
    def centroid(self) -> Tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    @property
    def width(self) -> int:
        return self.x2- self.x1


    @property
    def height(self) -> int:
        return self.y2-self.y1

    @ property
    def area(self ) -> int :
        return self.width *self.height

    def to_tlwh( self ) -> np.ndarray:
        # top, left, width, height for bytetrack
        return np.array([self.y1, self.x1, self.height, self.width], dtype=np.float32)

    def to_xyxy(self) -> np.ndarray:
        return np.array ( [self.x1 , self.y1,self.x2 ,self.y2],dtype=np.float32 )



class DetectorConfig:
    def __init__(self , path : str) :
        with open(path, "r") as f:
            cfg= yaml.safe_load( f)
        self.confidence_threshold :float =cfg.get("confidence_threshold" ,0.4)
        self.nms_threshold: float = cfg.get("nms_threshold", 0.45)
        self.input_size : Tuple [int,int]=tuple ( cfg.get ("input_size", [640 , 640 ] ) )
        self.hef_model_path: str = cfg.get("hef_model_path", "models/yolov8n-traffic.hef")
        self.onnx_model_path :str=cfg.get ( "onnx_model_path" ,"models/yolov8n-traffic.onnx")
        self.class_names :List [ str ]=cfg.get("class_names",CLASS_NAMES)
        self.enable_hailo :bool = cfg.get("enable_hailo" , True )


class HailoDetector:
    def __init__(self, config: DetectorConfig ) :
        self.config =config
        self._infer_model = None
        self._target=None
        self._configured_infer_model= None
        self._bindings = None
        self._load_model()

    def _load_model(self) -> None:

        from hailo_platform import(
            HEF ,
            VDevice ,
            HailoStreamInterface ,
            InferVStreams,
            ConfigureParams ,
            InputVStreamParams ,
            OutputVStreamParams,
            FormatType ,
        )

        logger.info("Loading Hailo HEF model: %s", self.config.hef_model_path)
        self._hef=HEF(self.config.hef_model_path )
        self._target = VDevice()
        network_group =self._target.configure( self._hef )[ 0]
        self._network_group= network_group

        self._input_vstreams_params =InputVStreamParams.make (
            self._network_group ,
            quantized =False,
            format_type =FormatType.FLOAT32,
        )
        self._output_vstreams_params=OutputVStreamParams.make (
            self._network_group,
            quantized =False ,
            format_type =FormatType.FLOAT32,
        )

        input_layer = self._hef.get_input_vstream_infos( )[ 0 ]
        self._input_name=input_layer.name
        h, w , c= input_layer.shape
        self._model_input_size = (w, h)
        logger.info(
            "Hailo model loaded. Input: %s shape=%s", self._input_name, (h, w, c)
        )

    def detect(self, frame: np.ndarray) -> List[Detection]:


        from hailo_platform import InferVStreams

        preprocessed, scale_x, scale_y, pad_x, pad_y = self._preprocess(frame)

        with InferVStreams(
            self._network_group ,
            self._input_vstreams_params,
            self._output_vstreams_params,
        )as infer_pipeline:
            input_data = {self._input_name: preprocessed[np.newaxis, ...]}
            with self._network_group.activate():
                infer_pipeline.send(input_data)
                raw_output = infer_pipeline.recv()

        # yolov8 output is [84, 8400]
        output = list(raw_output.values())[0][0]
        return self._postprocess ( output,frame.shape , scale_x,scale_y ,pad_x, pad_y)

    def _preprocess(
        self , frame :np.ndarray
    ) -> Tuple[np.ndarray, float, float, int, int]:
        target_w, target_h = self._model_input_size
        h, w = frame.shape[:2]

        scale = min (target_w /w, target_h/ h )
        new_w,new_h= int(w*scale),int ( h*scale)
        pad_x= (target_w - new_w )//2
        pad_y= ( target_h - new_h ) //2

        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas=np.full (( target_h,target_w, 3),114 , dtype =np.uint8)
        canvas[pad_y: pad_y + new_h, pad_x: pad_x + new_w] = resized


        #bgr to rgb and normalize to 0-1
        rgb =cv2.cvtColor(canvas , cv2.COLOR_BGR2RGB ).astype( np.float32 ) /255.0
        return rgb, 1.0 /scale , 1.0/ scale , pad_x, pad_y

    def _postprocess(
        self ,
        output: np.ndarray,
        original_shape :Tuple,
        scale_x: float,
        scale_y: float ,
        pad_x: int ,
        pad_y:int ,
    ) -> List[Detection]:
        orig_h ,orig_w = original_shape[ :2 ]
        target_w, target_h =self._model_input_size

        if output.shape[ 0] ==84 :
            output =output.T

        boxes ,scores , class_ids=[ ],[ ] , [ ]


        for det in output:
            cx , cy ,bw,bh= det [ :4]
            class_conf=det [ 4 :]
            class_id = int(np.argmax(class_conf))
            if class_id>=len ( self.config.class_names ) :
                continue
            confidence = float(class_conf[class_id ] )
            if confidence < self.config.confidence_threshold:
                continue

            #letterbox map back to orig
            x1= int ( ( cx- bw / 2 -pad_x ) *scale_x )
            y1=int( ( cy -bh/2 -pad_y )*scale_y)

            x2 = int(( cx+bw / 2 - pad_x) * scale_x )
            y2 = int((cy + bh / 2 - pad_y) * scale_y)

            x1 = max(0, min(x1, orig_w - 1))
            y1= max( 0,min( y1 , orig_h - 1 ))
            x2= max(0 ,min(x2, orig_w-1) )
            y2= max ( 0 ,min ( y2 , orig_h -1 ) )

            if x2<=x1 or y2<=y1 :
                continue

            boxes.append( [x1,y1 ,x2 -x1 ,y2 -y1] )
            scores.append ( confidence )


            class_ids.append (class_id)


        if not boxes:
            return []

        indices= cv2.dnn.NMSBoxes(
            boxes, scores , self.config.confidence_threshold , self.config.nms_threshold
        )
        detections = []
        for i in indices :


            idx =i[ 0]if isinstance ( i ,( list , np.ndarray ))else int(i)
            x, y, w, h = boxes[idx]
            detections.append(
                Detection(
                    class_id= class_ids[ idx] ,
                    label =self.config.class_names[class_ids [idx] ] ,
                    confidence=scores[idx],
                    x1 = x,
                    y1 =y ,
                    x2=x + w,
                    y2 = y + h,
                )
            )
        return detections

    def close(self)-> None:
        if self._target is not None:
            self._target.release ()


class OnnxDetector :

    def __init__(self , config: DetectorConfig) :
        self.config=config
        self._net= cv2.dnn.readNetFromONNX (config.onnx_model_path )
        #try cuda if opencv was built with it
        if cv2.cuda.getCudaEnabledDeviceCount ()>0 :
            self._net.setPreferableBackend (cv2.dnn.DNN_BACKEND_CUDA)
            self._net.setPreferableTarget (cv2.dnn.DNN_TARGET_CUDA)
            logger.info("OnnxDetector: using CUDA backend.")
        else:
            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV )
            self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)


            logger.info("OnnxDetector: using CPU backend.")
        self._input_size= config.input_size

    def detect (self ,frame: np.ndarray ) -> List[Detection] :
        h , w= frame.shape[ :2 ]
        iw , ih=self._input_size

        blob = cv2.dnn.blobFromImage(
            frame,
            scalefactor =1.0 / 255.0,
            size = (iw ,ih ),
            mean=(0, 0, 0),
            swapRB= True,
            crop =False,
        )
        self._net.setInput(blob)
        outputs=self._net.forward()
        output =outputs[ 0 ]

        scale_x=w / iw
        scale_y= h /ih

        if output.shape [ 0 ]== 84 :
            output =output.T

        boxes, scores ,class_ids=[], [],[]

        for det in output:
            cx,cy ,bw ,bh= det [ :4]
            class_conf = det[4:]

            class_id =int ( np.argmax( class_conf) )
            if class_id>= len ( self.config.class_names ) :
                continue
            confidence= float ( class_conf[ class_id] )
            if confidence < self.config.confidence_threshold:
                continue

            x1 =int ( ( cx-bw /2 ) *scale_x )
            y1 = int(( cy- bh/2 )*scale_y)
            x2 = int ( ( cx + bw /2)*scale_x )
            y2= int( ( cy+bh / 2)*scale_y)


            x1= max ( 0 , min( x1 , w - 1) )

            y1=max( 0 , min( y1,h -1) )
            x2 =max(0 ,min (x2 ,w -1))
            y2 =max ( 0 , min ( y2 ,h -1 ))
            if x2 <=x1 or y2 <= y1 :
                continue

            boxes.append([x1, y1, x2 - x1, y2 - y1])
            scores.append(confidence)
            class_ids.append (class_id )

        if not boxes :
            return[ ]

        indices = cv2.dnn.NMSBoxes(
            boxes, scores, self.config.confidence_threshold, self.config.nms_threshold
        )
        detections = []
        for i in indices :
            idx = i[0] if isinstance(i, (list, np.ndarray)) else int(i)
            x, y ,bw,bh = boxes[idx]
            detections.append(
                Detection(
                    class_id=class_ids[idx],
                    label =self.config.class_names [class_ids [ idx] ] ,
                    confidence=scores [ idx],
                    x1=x ,
                    y1=y ,
                    x2=x + bw,
                    y2= y+bh,
                )
            )
        #logger.debug("detected %d objects", len(detections))
        return detections

    def close(self) -> None:
        pass


def create_detector( config_path : str = "config/detection.yaml") :
    config=DetectorConfig(config_path )


    if config.enable_hailo:
        try:
            import hailo_platform  # noqa: F401

            if os.path.exists(config.hef_model_path):
                logger.info ("Hailo platform available, using HailoDetector." )
                return HailoDetector(config)
            else:
                logger.warning(
                    "HEF model not found at '%s', falling back to ONNX." ,
                    config.hef_model_path,
                )
        except ImportError :
            #expected when running off-target
            logger.warning("hailo_platform not installed, falling back to ONNX detector." )
    else:
        logger.info("Hailo disabled in config, using ONNX detector.")

    if not os.path.exists(config.onnx_model_path) :
        raise FileNotFoundError (
            f"ONNX model not found at '{config.onnx_model_path}'. "
            "Please download or train the model first."
        )

    logger.info ( "Using OnnxDetector (CPU/CUDA)." )
    return OnnxDetector (config)
