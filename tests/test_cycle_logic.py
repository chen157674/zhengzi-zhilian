"""12 通用周期契约；数值是显式测试参数，不是通用人体标准或识别真值。"""
from dataclasses import replace
import unittest
from fitness_app.cycles import CycleConfig,CycleTracker


CONFIG=CycleConfig(start_threshold=100,direction=1,exit_delta=10,minimum_excursion=20,turn_delta=5,
    start_confirm_ms=100,departure_confirm_ms=100,turn_confirm_ms=100,return_confirm_ms=100,
    maximum_gap_ms=250,minimum_cycle_ms=500,maximum_cycle_ms=3000)
COMPLETE=[(0,100),(100,100),(200,112),(300,125),(400,140),(500,130),(600,120),(700,100),(800,100)]


def consume(tracker,sequence):
    output=[]
    for stamp,value in sequence:
        result=tracker.observe(value,stamp)
        if result:output.append(result)
    return output


class CycleTests(unittest.TestCase):
    def test_complete_returns_once_and_incomplete_or_middle_start_stays_zero(self):
        tracker=CycleTracker(CONFIG);events=consume(tracker,COMPLETE)
        self.assertEqual(len(events),1);self.assertEqual((events[0]['start_ms'],events[0]['end_ms'],events[0]['extreme_ms']),(200,800,400))
        self.assertIsNone(tracker.observe(100,800));self.assertEqual(consume(tracker,[(900,100),(1000,100)]),[])
        self.assertEqual(consume(CycleTracker(CONFIG),COMPLETE[:5]),[])
        self.assertEqual(consume(CycleTracker(CONFIG),COMPLETE[3:]),[])
        self.assertEqual(len(consume(CycleTracker(CONFIG),COMPLETE+[(t+900,v) for t,v in COMPLETE])),2)

    def test_noise_partial_range_and_short_reversal(self):
        noise=[(index*100,value) for index,value in enumerate([100,100,102,108,105,101,100,100])]
        self.assertEqual(consume(CycleTracker(CONFIG),noise),[])
        # 已超过分段最小运动且回到起点，即使未达到另一个质量阈值，也应有周期。
        smaller=[(t,100+(v-100)*.6) for t,v in COMPLETE]
        self.assertEqual(len(consume(CycleTracker(CONFIG),smaller)),1)
        bounce=COMPLETE[:5]+[(500,130),(600,141),(700,135),(800,125),(900,100),(1000,100)]
        events=consume(CycleTracker(CONFIG),bounce);self.assertEqual(len(events),1);self.assertEqual(events[0]['extreme_ms'],600)

    def test_pause_missing_gap_and_out_of_order_cannot_join_halves(self):
        tracker=CycleTracker(CONFIG);consume(tracker,COMPLETE[:5]);tracker.reset('paused')
        self.assertEqual(consume(tracker,COMPLETE[5:]),[])
        tracker=CycleTracker(CONFIG);consume(tracker,COMPLETE[:5]);tracker.observe(None,450)
        self.assertEqual(consume(tracker,COMPLETE[5:]),[])
        tracker=CycleTracker(CONFIG);consume(tracker,COMPLETE[:5])
        self.assertEqual(consume(tracker,[(t+1000,v) for t,v in COMPLETE[5:]]),[])
        tracker=CycleTracker(CONFIG);consume(tracker,COMPLETE[:5]);tracker.observe(100,250)
        self.assertEqual(consume(tracker,COMPLETE[5:]),[])

    def test_direction_and_source_time_independent_of_delivery_speed(self):
        increasing=consume(CycleTracker(CONFIG),COMPLETE)
        decreasing=consume(CycleTracker(replace(CONFIG,direction=-1)),[(t,200-v) for t,v in COMPLETE])
        self.assertEqual([(e['start_ms'],e['end_ms']) for e in increasing],[(e['start_ms'],e['end_ms']) for e in decreasing])
        # 每个源帧重复送达，处理调用次数变化也不增加事件。
        repeated=[item for item in COMPLETE for unused in range(3)]
        self.assertEqual(consume(CycleTracker(CONFIG),repeated),increasing)

    def test_departure_and_return_use_observed_anchor_not_wide_start_region(self):
        tracker=CycleTracker(CONFIG)
        partial=[(index*100,value) for index,value in enumerate([50,50,65,80,130,140,130,110,90,90,90])]
        self.assertEqual(consume(tracker,partial),[],'回到全局起点区域还不等于返回本次起点')
        events=consume(tracker,[(1100,60),(1200,50),(1300,50)])
        self.assertEqual(len(events),1);self.assertEqual((events[0]['start_ms'],events[0]['end_ms'],events[0]['start_value']),(200,1300,50))


if __name__=='__main__':unittest.main()
