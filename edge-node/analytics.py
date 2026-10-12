import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import yaml

from tracker import Track

logger=logging.getLogger ( __name__ )




@dataclass
class CountingLine :
    line_id :str
    start: Tuple[int, int]
    end: Tuple[ int, int ]

    direction:str  # 'up', 'down', 'left', 'right'
    count : int= 0


@dataclass
class ParkingSpot:
    spot_id:str
    roi: Tuple[int, int, int, int]  # x1, y1, x2, y2


    occupied : bool =False
    occupied_since:Optional[float]=None  #monotonic timestamp
    last_track_id: Optional[int] = None


@dataclass
class SpeedSample:
    track_id: int
    speed_kmh :float

    timestamp: float


@dataclass
class AnalyticsSummary:
    counts_by_class : Dict[str , int]
    line_counts :Dict [str, int]
    parking:Dict[ str , bool]
    avg_speed_kmh : float
    speed_samples: List[SpeedSample]
    timestamp: float = field(default_factory=time.time)



class AnalyticsEngine :
    def __init__(
        self ,
        counting_lines: List[CountingLine],
        parking_spots:List [ ParkingSpot],
        pixels_per_metre: float = 10.0 ,
        fps: float = 30.0,
        parking_threshold_seconds: float=5.0 ,
    ):
        self.counting_lines=counting_lines
        self.parking_spots = parking_spots
        self.pixels_per_metre =pixels_per_metre
        self.fps = fps
        self.parking_threshold_seconds =parking_threshold_seconds

        # track previous centroid for line crossing calculations
        self._track_prev_centroid:Dict [int, Tuple [ int , int]] = { }
        self._counts_by_class: Dict[str, int] = defaultdict(int)
        self._speed_samples :List [SpeedSample ] =[ ]
        # self._min_dist_px = 0.5

    @classmethod
    def from_configs(
        cls,
        counting_lines_path: str = "config/counting_lines.yaml",
        parking_spots_path :str="config/parking_spots.yaml",
    )->"AnalyticsEngine" :
        with open(counting_lines_path , "r" ) as f:
            cl_cfg =yaml.safe_load (f )
        lines = [
            CountingLine(
                line_id =l ["line_id"],
                start=tuple(l["start"]),
                end =tuple( l [ "end"]),
                direction=l["direction"],
            )
            for l in cl_cfg.get("counting_lines", [ ] )
        ]

        with open(parking_spots_path, "r") as f:
            ps_cfg=yaml.safe_load (f )
        spots = [
            ParkingSpot ( spot_id =s["spot_id" ] , roi=tuple (s [ "roi" ]))
            for s in ps_cfg.get("parking_spots",[] )
        ]
        threshold= ps_cfg.get ("occupied_threshold_seconds", 5.0 )

        return cls (
            counting_lines=lines,
            parking_spots =spots ,
            parking_threshold_seconds = threshold ,
        )

    def process(self,tracks : List [Track ] , frame_timestamp : float )->None:

        active_track_ids= { t.track_id for t in tracks}

        for track in tracks:
            prev = self._track_prev_centroid.get( track.track_id)
            curr = track.centroid

            if prev is not None:
                for line in self.counting_lines:
                    if self._crosses_line(prev, curr, line):
                        self._counts_by_class[track.label] += 1
                        line.count += 1
                        logger.debug(
                            "Line '%s' crossed by track %d (%s). Count: %d" ,
                            line.line_id,
                            track.track_id ,
                            track.label,
                            line.count,
                        )

                speed = self._estimate_speed(prev, curr)
                if speed> 0 :
                    self._speed_samples.append(
                        SpeedSample (
                            track_id=track.track_id ,
                            speed_kmh=speed,
                            timestamp=frame_timestamp,
                        )
                    )

            self._track_prev_centroid[track.track_id] = curr



        self._update_parking(tracks, frame_timestamp)

        #drop tracks that went out of frame
        stale= set( self._track_prev_centroid.keys( ) )-active_track_ids
        for tid in stale :
            del self._track_prev_centroid [tid ]
            #free any parking spots held by vanished track
            for spot in self.parking_spots:
                if spot.last_track_id == tid:
                    spot.occupied = False
                    spot.occupied_since = None
                    spot.last_track_id=None

    def get_summary( self ) ->AnalyticsSummary :
        line_counts = {l.line_id: l.count for l in self.counting_lines}
        parking ={s.spot_id: s.occupied for s in self.parking_spots }

        if self._speed_samples:
            avg_speed=float(
                np.mean( [ s.speed_kmh for s in self._speed_samples])
            )
        else :
            avg_speed =0.0

        return AnalyticsSummary(
            counts_by_class=dict(self._counts_by_class),
            line_counts =line_counts,
            parking =parking,
            avg_speed_kmh = round (avg_speed , 2 ),
            speed_samples =list( self._speed_samples) ,
        )

    def get_parking_state (self)-> Dict[str,bool]:
        return{ s.spot_id :s.occupied for s in self.parking_spots}

    def reset_counts(self )-> None :

        #clear batch stats after sending over mqtt
        self._counts_by_class.clear ()
        self._speed_samples.clear()
        for line in self.counting_lines:
            line.count= 0

    def set_pixels_per_metre(self, ppm: float) -> None:
        self.pixels_per_metre = ppm

    @ staticmethod
    def _side_of_line (
        pt: Tuple[int, int],
        line_start: Tuple[int, int],
        line_end :Tuple[int ,int ],
    )->float :
        #cross product to find which side of vector the point is on
        lx=line_end [ 0 ]-line_start[ 0]

        ly=line_end [ 1 ]- line_start [ 1]
        px= pt [0 ] -line_start [ 0 ]
        py= pt [ 1]-line_start [1]
        return lx * py- ly *px

    def _crosses_line(
        self ,
        prev :Tuple [ int, int] ,
        curr: Tuple [int, int] ,
        line: CountingLine,
    )-> bool :
        prev_side= self._side_of_line( prev ,line.start, line.end)
        curr_side = self._side_of_line(curr, line.start, line.end)

        if prev_side * curr_side >= 0:
            return False  # still on same side, no crossing occured

        dy = curr[1] - prev[1]
        dx = curr [0 ] - prev[0]


        # check direction filter
        if line.direction == "up" and dy >=0:
            return False
        if line.direction == "down" and dy <= 0:
            return False
        if line.direction=="left" and dx >= 0 :
            return False
        if line.direction=="right" and dx<= 0 :
            return False

        return True

    def _estimate_speed(
        self ,
        prev : Tuple [int,int] ,
        curr : Tuple[ int , int ],
    ) -> float :


        # distance in pixels between consecutive frames
        px_dist= float (
            np.sqrt( (curr[0 ] - prev[0] )** 2+(curr [ 1]- prev[1 ] ) ** 2)
        )

        # ignore tiny jitters below half a pixel
        if px_dist <0.5 or self.pixels_per_metre <=0 :
            return 0.0
        # px_dist / ppm gives meters, * fps gives m/s, * 3.6 gives km/h
        m_per_s= ( px_dist / self.pixels_per_metre ) *self.fps
        return round( m_per_s*3.6 ,2)

    def _update_parking(self ,tracks:List [Track ] , now :float ) -> None :


        occupied_spots :Dict [ str,int]= {}

        for track in tracks:
            # pedestrians dont occupy parking spaces
            if track.label=="pedestrian":
                continue
            cx ,cy =track.centroid
            for spot in self.parking_spots :
                x1, y1, x2, y2 = spot.roi
                if x1<= cx<=x2 and y1 <= cy <= y2 :

                    occupied_spots[spot.spot_id] =track.track_id

        for spot in self.parking_spots:
            if spot.spot_id in occupied_spots :
                tid = occupied_spots[spot.spot_id]
                if spot.last_track_id!=tid :
                    #new car entered the spot
                    spot.last_track_id = tid
                    spot.occupied_since = now
                    spot.occupied = False
                else :
                    # must remain in spot for threshold time before marked occupied
                    if(
                        spot.occupied_since is not None
                        and (now - spot.occupied_since) >= self.parking_threshold_seconds
                    ):


                        spot.occupied =True
            else:
                spot.occupied = False
                spot.occupied_since =None
                spot.last_track_id = None
