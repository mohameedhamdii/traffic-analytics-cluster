import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict,List,Optional , Tuple

import numpy as np
import yaml

logger =logging.getLogger ( __name__ )


class KalmanBoxTracker :
    count=0  # global counter across tracker instances



    def __init__(self, bbox_xyxy: np.ndarray):
        KalmanBoxTracker.count +=1
        self.track_id:int = KalmanBoxTracker.count

        dt = 1.0  #assuming constant 1-frame timestep


        # state vector is [cx, cy, s, r, vx, vy, vs]
        self._F = np.array([
            [1, 0,0 , 0 ,dt ,0 ,0] ,
            [0, 1 , 0 , 0,0 , dt , 0] ,
            [0, 0, 1, 0,  0,  0, dt],
            [0 , 0, 0 ,1, 0,0,0],
            [ 0,0 , 0 , 0, 1, 0, 0] ,
            [0, 0, 0, 0,  0,  1,  0],
            [0, 0, 0, 0,  0,  0,  1],
        ], dtype=np.float32)

        self._H = np.array([
            [1 ,0,0 ,0, 0 , 0,0],
            [0, 1, 0, 0, 0, 0, 0],
            [0, 0, 1, 0, 0, 0, 0],
            [0, 0 , 0,1 ,0 , 0 , 0 ] ,
        ],dtype= np.float32 )

        self._Q =np.diag(
            [ 1.0, 1.0 , 10.0,10.0,0.01,0.01, 0.0001]
        ).astype ( np.float32)

        self._R = np.diag([1.0, 1.0, 10.0, 10.0]).astype(np.float32)

        self._P = np.diag(
            [10.0 , 10.0 ,10.0,10.0 ,10000.0 , 10000.0 , 10000.0]
        ).astype(np.float32)



        self._x=self._bbox_to_z( bbox_xyxy )

    @staticmethod
    def _bbox_to_z (bbox_xyxy : np.ndarray ) -> np.ndarray :
        x1,y1,x2 , y2=bbox_xyxy
        cx = (x1 + x2) / 2.0
        cy = (y1 +y2) / 2.0
        s = float((x2 - x1) * (y2 - y1))

        r= float ( (x2- x1)/max( 1, y2- y1 ))
        return np.array( [ [cx ], [ cy],[ s ] ,[ r ],[0 ] ,[0], [ 0]] , dtype=np.float32 )

    @staticmethod
    def _z_to_bbox(z: np.ndarray) -> np.ndarray:
        flat= z.flatten()
        cx , cy ,s , r =float (flat[ 0] ) ,float(flat [1]),float(flat [ 2]),float (flat [ 3 ])
        w = np.sqrt(max(0, s * r))
        h=max ( 0 , s / max(1e-6, w) )
        return np.array([cx -w/2 , cy -h/2 ,cx+w / 2 ,cy + h /2] )


    def predict (self ) ->np.ndarray :
        if self._x[2] + self._x[6] <= 0:
            self._x[6] = 0
        self._x = self._F@ self._x
        self._P = self._F@self._P@ self._F.T+self._Q
        return self._z_to_bbox ( self._x)

    def update ( self,bbox_xyxy : np.ndarray) -> None:
        z = self._bbox_to_z(bbox_xyxy)[:4]
        y =z -self._H @ self._x
        S=self._H @self._P @self._H.T + self._R
        K = self._P @ self._H.T @ np.linalg.inv(S)
        self._x= self._x+ K @y
        self._P = (np.eye(7) - K @ self._H) @ self._P

    def get_bbox( self)-> np.ndarray :
        return self._z_to_bbox(self._x)


class TrackState ( Enum) :
    TENTATIVE = auto()
    CONFIRMED=auto ( )
    LOST =auto()
    DELETED= auto ( )



@dataclass
class Track:
    track_id: int
    class_id : int
    label : str
    kalman :KalmanBoxTracker
    state:TrackState= TrackState.TENTATIVE
    hits : int= 1
    age: int = 1
    time_since_update: int = 0
    history: List[Tuple[int, int]] = field(default_factory=list)

    @property
    def bbox(self) -> np.ndarray:
        return self.kalman.get_bbox()

    @ property
    def centroid ( self) -> Tuple [ int, int ]:
        b = self.bbox


        return (int(( b [ 0]+b [2 ])/2 ) ,int( (b[ 1 ] + b[ 3 ] )/ 2) )

    def predict(self) -> None:
        self.kalman.predict()
        self.age += 1
        self.time_since_update+=1

    def update(self, bbox_xyxy: np.ndarray, label: str, class_id: int) -> None:
        self.kalman.update( bbox_xyxy )
        self.time_since_update = 0
        self.hits += 1
        self.class_id = class_id
        self.label = label
        cx, cy = self.centroid
        self.history.append(( cx,cy ))
        if len(self.history) >30:
            self.history.pop( 0)

    def to_dict ( self ) ->dict :
        b = self.bbox
        b_int = [ int (b [ 0]),int (b [1]) ,int (b [2 ]), int ( b [ 3])]
        cx, cy = self.centroid

        return {
            "track_id":self.track_id ,
            "class": self.label,
            "class_id":self.class_id ,
            "bbox": b_int,
            "centroid" : [ cx , cy ],
            "confidence": 1.0,
            "state": self.state.name ,
        }


def iou_batch (bboxes_a : np.ndarray , bboxes_b:np.ndarray)->np.ndarray:
    if len (bboxes_a )== 0 or len (bboxes_b)== 0:
        return np.zeros((len(bboxes_a), len(bboxes_b)), dtype=np.float32)

    A= np.expand_dims ( bboxes_a ,1 )
    B= np.expand_dims ( bboxes_b , 0)

    inter_x1=np.maximum(A [..., 0] ,B[ ... ,0 ])
    inter_y1 =np.maximum(A [ ..., 1 ] ,B[... , 1])

    inter_x2 =np.minimum(A[... ,2],B [ ..., 2])
    inter_y2 = np.minimum ( A[..., 3 ] ,B [... ,3 ] )

    inter_w =np.maximum(0 ,inter_x2 - inter_x1)
    inter_h = np.maximum( 0, inter_y2-inter_y1 )
    inter_area = inter_w * inter_h

    area_a= (bboxes_a[:,2] -bboxes_a[ : ,0] ) *( bboxes_a [: , 3 ]- bboxes_a [ :,1 ] )
    area_b=(bboxes_b[: ,2]-bboxes_b[:, 0] )*( bboxes_b [ :,3 ]- bboxes_b [: ,1 ])

    union_area = (
        np.expand_dims ( area_a , 1 )+ np.expand_dims ( area_b, 0) - inter_area
    )
    return inter_area/ np.maximum ( union_area,1e-6 )


def linear_assignment( cost_matrix :np.ndarray ) -> np.ndarray:
    try:

        from scipy.optimize import linear_sum_assignment

        row_ind, col_ind = linear_sum_assignment(cost_matrix)
        return np.array( list( zip(row_ind ,col_ind)) )
    except ImportError :
        # greedy fallback if scipy isnt installed
        matches = [ ]
        used_rows , used_cols=set() ,set ()
        indices = np.argsort(cost_matrix , axis=None)
        for idx in indices:
            r, c = divmod ( idx, cost_matrix.shape [ 1 ] )
            if r not in used_rows and c not in used_cols:
                matches.append ((r, c ) )


                used_rows.add( r)

                used_cols.add( c)
        return np.array(matches) if matches else np.empty((0, 2), dtype=int)


class ByteTracker:
    def __init__ (
        self,
        max_age: int = 30,
        min_hits: int = 3,
        iou_threshold :float=0.3 ,
        high_score_threshold: float= 0.6,
        low_score_threshold: float = 0.1,
    ) :
        self.max_age=max_age


        self.min_hits = min_hits
        self.iou_threshold= iou_threshold
        self.high_score_threshold= high_score_threshold
        self.low_score_threshold = low_score_threshold

        self._tracks : List [ Track] =[ ]
        self._frame_count= 0
        KalmanBoxTracker.count= 0

    @ classmethod
    def from_config ( cls, config_path :str ) ->"ByteTracker" :

        with open (config_path,"r" ) as f:
            cfg=yaml.safe_load( f)
        return cls (
            max_age=cfg.get("max_age", 30),
            min_hits= cfg.get ("min_hits" , 3),
            iou_threshold = cfg.get("iou_threshold",0.3 ),
            high_score_threshold= cfg.get( "high_score_threshold",0.6) ,
            low_score_threshold=cfg.get("low_score_threshold", 0.1),
        )

    def update( self , detections ) ->List [Track]:
        self._frame_count+= 1

        for track in self._tracks:
            track.predict( )

        high_dets = [d for d in detections if d.confidence >= self.high_score_threshold]
        low_dets= [d for d in detections if self.low_score_threshold <=d.confidence <self.high_score_threshold]



        #stage 1: associate high-score detections with tracks
        active_tracks =[t for t in self._tracks if t.state!= TrackState.DELETED ]
        matched_h , unmatched_tracks , unmatched_high_dets=self._match (
            high_dets, active_tracks
        )

        for track_idx,det_idx in matched_h:
            d= high_dets[det_idx ]
            active_tracks[track_idx].update(d.to_xyxy(), d.label, d.class_id)
            if(
                active_tracks [ track_idx].hits>= self.min_hits
                or self._frame_count<= self.min_hits
            ):
                active_tracks [ track_idx ].state = TrackState.CONFIRMED

        # stage 2: rescue lost tracks with lower confidence detections
        lost_tracks= [ active_tracks[ i] for i in unmatched_tracks if active_tracks[i].state ==TrackState.LOST]
        matched_l ,_ , unmatched_low_dets = self._match(low_dets , lost_tracks)

        for track_idx,det_idx in matched_l :
            d =low_dets [det_idx]
            lost_tracks[track_idx].update(d.to_xyxy(), d.label, d.class_id)
            if lost_tracks [track_idx].hits >= self.min_hits:
                lost_tracks[track_idx].state=TrackState.CONFIRMED

        #mark missing tracks as lost or delete if expired
        remaining_unmatched_track_indices =set ( unmatched_tracks)
        for i in remaining_unmatched_track_indices:
            track=active_tracks [ i]

            if track.state== TrackState.CONFIRMED:
                track.state= TrackState.LOST
            if track.time_since_update > self.max_age:
                track.state =TrackState.DELETED

        # init new tracks for remaining confident detections
        for det_idx in unmatched_high_dets:
            d =high_dets[det_idx]
            kalman =KalmanBoxTracker(d.to_xyxy())
            new_track =Track (
                track_id=kalman.track_id,
                class_id=d.class_id,
                label = d.label,
                kalman= kalman,
            )
            new_track.history.append (
                ((d.x1 + d.x2) // 2, (d.y1 + d.y2) // 2)
            )
            if new_track.hits >= self.min_hits or self._frame_count <= self.min_hits:

                new_track.state = TrackState.CONFIRMED

            self._tracks.append(new_track )

        self._tracks = [t for t in self._tracks if t.state != TrackState.DELETED]
        return [t for t in self._tracks if t.state == TrackState.CONFIRMED]

    def _match (
        self, detections, tracks: List[Track]
    ) -> Tuple[np.ndarray, List[int], List[int]]:
        if not detections or not tracks :
            return(
                np.empty (( 0 ,2 ) , dtype= int) ,
                list( range (len(tracks )) ) ,
                list(range(len(detections))),
            )

        track_bboxes =np.array ( [t.bbox for t in tracks] , dtype =np.float32 )
        det_bboxes = np.array([d.to_xyxy() for d in detections], dtype=np.float32)

        iou_matrix= iou_batch (track_bboxes , det_bboxes)
        cost_matrix= 1.0- iou_matrix

        matched_indices = linear_assignment( cost_matrix )

        matches, unmatched_tracks , unmatched_dets= [],[ ],[ ]

        matched_track_set = set()
        matched_det_set = set()

        for t_idx, d_idx in matched_indices:
            if iou_matrix [t_idx,d_idx]>=self.iou_threshold:
                matches.append((t_idx, d_idx))
                matched_track_set.add( t_idx )
                matched_det_set.add ( d_idx)

        unmatched_tracks = [i for i in range ( len( tracks )) if i not in matched_track_set ]
        unmatched_dets = [ i for i in range ( len(detections))if i not in matched_det_set]


        return np.array(matches, dtype=int), unmatched_tracks, unmatched_dets

    @property
    def track_count( self) ->int:
        return len(self._tracks )

    def reset ( self) -> None :
        self._tracks =[]
        self._frame_count = 0
        KalmanBoxTracker.count=0
