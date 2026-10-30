"""按源时间确认完整周期。适配器显式提供分段参数，不使用质量合格阈值。

结构参考 MIT 上游 BaseExercise 的阶段、去抖和最小周期字段；改为转向后
返回起点才输出事件，不复用其到达极值即计数、墙钟计时或综合评分。
来源：third_party/fitness-trainer-pose-estimation/exercises/base_exercise.py。
"""
from dataclasses import dataclass
import math
import statistics


@dataclass(frozen=True)
class CycleConfig:
    start_threshold:float
    direction:int
    exit_delta:float
    minimum_excursion:float
    turn_delta:float
    start_confirm_ms:int
    departure_confirm_ms:int
    turn_confirm_ms:int
    return_confirm_ms:int
    maximum_gap_ms:int
    minimum_cycle_ms:int
    maximum_cycle_ms:int
    return_tolerance:float|None=None

    def __post_init__(self):
        if type(self.direction) is not int or self.direction not in (-1,1):raise ValueError('分段方向必须为 -1 或 1')
        values=(self.start_threshold,self.exit_delta,self.minimum_excursion,self.turn_delta)
        if any(type(value) not in (int,float) or not math.isfinite(value) for value in values):raise ValueError('分段参数必须是有限数')
        if not 0<self.exit_delta<=self.minimum_excursion or not 0<self.turn_delta<self.minimum_excursion:raise ValueError('退出／转向分段带无效')
        times=(self.start_confirm_ms,self.departure_confirm_ms,self.turn_confirm_ms,self.return_confirm_ms,self.maximum_gap_ms,self.minimum_cycle_ms,self.maximum_cycle_ms)
        if any(type(value) is not int or value<=0 for value in times) or self.minimum_cycle_ms>=self.maximum_cycle_ms:raise ValueError('源时间分段参数无效')
        if self.return_tolerance is not None and (type(self.return_tolerance) not in (int,float) or not math.isfinite(self.return_tolerance) or self.return_tolerance<=0):raise ValueError('返回确认带无效')


class CycleTracker:
    def __init__(self,config:CycleConfig):
        self.config=config;self.last_stamp=None;self.reset('created')

    def _clear(self,reason):
        self.phase='seeking_start';self.reason=reason;self.stable_since=None;self.anchor=None
        self.departure_since=None;self.turn_since=None;self.return_since=None;self.started_ms=None
        self.extreme=None;self.extreme_ms=None;self.maximum_excursion=0.
        self.stable_values=[];self.rest_since=None;self.rest_values=[]

    def reset(self,reason):
        self.last_stamp=None;self._clear(reason)

    def _start(self,value):return self.config.direction*(value-self.config.start_threshold)<=0

    def _seek(self,value,stamp):
        if not self._start(value):self.stable_since=None;self.stable_values=[];return
        if self.stable_since is None:self.stable_since=stamp
        self.stable_values.append(value)
        if max(self.stable_values)-min(self.stable_values)>self.config.exit_delta:
            self.stable_since=stamp;self.stable_values=[value]
        if stamp-self.stable_since>=self.config.start_confirm_ms:
            self.phase='ready';self.reason=None;self.anchor=float(statistics.median(self.stable_values));self.stable_values=[]

    def observe(self,value,stamp,return_allowed=True):
        if type(stamp) is not int or stamp<0:
            self._clear('invalid_timestamp');return None
        if self.last_stamp is not None:
            if stamp==self.last_stamp:return None  # 同一输入不重复改变阶段。
            if stamp<self.last_stamp:
                self._clear('out_of_order');return None
            if stamp-self.last_stamp>self.config.maximum_gap_ms:self._clear('source_gap')
        self.last_stamp=stamp
        if type(value) not in (int,float) or not math.isfinite(value):
            self._clear('unobservable');return None
        if self.phase=='seeking_start':self._seek(value,stamp);return None
        progress=self.config.direction*(value-self.anchor)
        if self.phase=='ready':
            self.reason=None
            if progress>=self.config.exit_delta:
                self.phase='departing';self.departure_since=stamp;self.started_ms=stamp
                self.extreme=value;self.extreme_ms=stamp;self.maximum_excursion=progress
                self.rest_since=None;self.rest_values=[]
            elif progress<0 and self._start(value):
                if self.rest_since is None:self.rest_since=stamp
                self.rest_values.append(value)
                if max(self.rest_values)-min(self.rest_values)>self.config.exit_delta:
                    self.rest_since=stamp;self.rest_values=[value]
                if stamp-self.rest_since>=self.config.start_confirm_ms:
                    # 只在更靠起点一侧的新稳定区更新，不追随正在离开的身体。
                    self.anchor=float(statistics.median(self.rest_values));self.rest_since=None;self.rest_values=[]
            else:self.rest_since=None;self.rest_values=[]
            return None
        if stamp-self.started_ms>self.config.maximum_cycle_ms:
            self._clear('cycle_too_long');self._seek(value,stamp);return None
        if progress>self.maximum_excursion:
            self.maximum_excursion=progress;self.extreme=value;self.extreme_ms=stamp
            self.turn_since=None
            if self.phase in {'returning','confirming_return'}:
                self.phase='moving';self.return_since=None
        if self.phase=='departing':
            if progress<self.config.exit_delta:
                self._clear('departure_not_confirmed');self._seek(value,stamp);return None
            if stamp-self.departure_since>=self.config.departure_confirm_ms:self.phase='moving'
        if self.phase=='moving':
            reversal=self.maximum_excursion-progress
            if self.maximum_excursion>=self.config.minimum_excursion and reversal>=self.config.turn_delta:
                if self.turn_since is None:self.turn_since=stamp
                if stamp-self.turn_since>=self.config.turn_confirm_ms:self.phase='returning'
            else:self.turn_since=None
        if self.phase in {'returning','confirming_return'}:
            tolerance=self.config.return_tolerance if self.config.return_tolerance is not None else self.config.exit_delta/2
            if self._start(value) and progress<=tolerance:
                if not return_allowed:
                    self._clear('return_posture_unconfirmed');return None
                if self.return_since is None:self.return_since=stamp
                self.phase='confirming_return'
                if stamp-self.return_since>=self.config.return_confirm_ms:
                    completed=stamp-self.started_ms>=self.config.minimum_cycle_ms
                    event={'start_ms':self.started_ms,'end_ms':stamp,'extreme_ms':self.extreme_ms,'extreme_value':self.extreme,'excursion':self.maximum_excursion,'start_value':self.anchor} if completed else None
                    anchor=self.anchor;self._clear('completed' if completed else 'cycle_too_short')
                    self.phase='ready';self.anchor=anchor
                    return event
            else:
                self.return_since=None;self.phase='returning'
        return None
