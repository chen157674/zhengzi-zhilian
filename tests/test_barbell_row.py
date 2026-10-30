"""14-H 专属周期／变式反例，复用公共周期引擎既有边界测试。"""
import unittest

from fitness_app.barbell_row import BarbellRowConfig,BarbellRowCounter
from fitness_app.cycles import CycleConfig


def settings():
    return BarbellRowConfig(CycleConfig(145.,-1,12.,25.,8.,200,80,80,100,500,600,30000,15.),30.,20.,100.,80.,.6,.15,3,'D-POSE-barbell-row-test')


def geometry(level,seq=0,standing=False,sway=0.):
    points=[dict(x=.5,y=.5,z=0.,visibility=.99,presence=.99,valid=True) for unused in range(33)]
    for side in (0,1):
        coords={11:(.62 if standing else .38+sway,.25 if standing else .38),13:(.38+.16*level,.56-.08*level),
            15:(.38+.10*level,.74-.16*level),23:(.62,.48),25:(.57,.72),27:(.61,.94),31:(.55,.96)}
        for i,(x,y) in coords.items():points[i+side].update(x=x+side*.025,y=y)
    return {'frame_seq':seq,'captured_ms':seq*150,'width':1000,'height':1000,'framing':{'person_count':1,'landmarks':points}}


def sequence():
    return [geometry(v,i) for i,v in enumerate([0]*6+[.2,.4,.6,.8,1,1,1,.8,.6,.4,.2]+[0]*5)]


def consume(frames,counter=None):
    counter=counter or BarbellRowCounter(settings());events=[];reasons=[]
    for f in frames:
        result=counter.process(f,{'stream_epoch':'row-fixture','identity_accepted':f.get('identity_accepted',True)})
        events.extend(result['events']);reasons.append(result['observation']['sides']['none']['reason'])
    return events,reasons


class BarbellRowTests(unittest.TestCase):
    def test_complete_bent_start_pull_return_and_half_cycle(self):
        events,_=consume(sequence());self.assertEqual(len(events),1);self.assertEqual(events[0]['side'],'none');self.assertEqual(events[0]['assessments'],[])
        self.assertEqual(consume(sequence()[:13])[0],[]);self.assertNotIn('bar_path',events[0])

    def test_standing_exit_cannot_complete_or_reanchor_a_row(self):
        frames=sequence()
        for i in range(13,len(frames)):frames[i]=geometry(0,i,standing=True)
        events,reasons=consume(frames);self.assertEqual(events,[]);self.assertIn('bent_row_variant_unconfirmed',reasons)
        self.assertEqual(consume([geometry(0,i,standing=True) for i in range(25)])[0],[])

    def test_wrist_or_foot_occlusion_discards_inflight_cycle(self):
        for part in (15,31):
            frames=sequence();frames[11]['framing']['landmarks'][part]['valid']=False
            self.assertEqual(consume(frames)[0],[])

    def test_moderate_trunk_sway_does_not_silently_become_quality_veto(self):
        frames=sequence()
        for i in range(6,17):
            for s in (0,1):frames[i]['framing']['landmarks'][11+s]['x']+=.02
        self.assertEqual(len(consume(frames)[0]),1)

    def test_body_translation_without_arm_pull_is_not_a_row(self):
        frames=sequence()
        for i in range(len(frames)):
            frames[i]=geometry(0,i)
            for p in frames[i]['framing']['landmarks']:p['x']+=.05*min(i,22-i)/11
        self.assertEqual(consume(frames)[0],[])

    def test_countdown_keeps_only_ready_start(self):
        counter=BarbellRowCounter(settings());frames=sequence()
        for f in frames[:6]:self.assertEqual(counter.prime(f,{'stream_epoch':'row-fixture'})['events'],[])
        counter.begin();tail=[{**f,'captured_ms':f['captured_ms']-900} for f in frames[6:]]
        self.assertEqual(len(consume(tail,counter)[0]),1)

    def test_clearer_side_is_reselected_only_during_confirmed_preparation(self):
        counter=BarbellRowCounter(settings())
        frames=[geometry(0,i) for i in range(14)]
        for i,f in enumerate(frames):
            for p in (12,14,16,24,26,28,32):f['framing']['landmarks'][p]['presence']=.8
            if i<3:f['framing']['landmarks'][15]['valid']=False
        consume(frames,counter);self.assertEqual(counter.measurement_side,'left');self.assertEqual(counter.tracker.phase,'ready')
        moving=sequence()[6:]
        for i,f in enumerate(moving,14):
            f.update(frame_seq=i,captured_ms=i*150)
            for p in (11,13,15,23,25,27,31):f['framing']['landmarks'][p]['presence']=.8
        self.assertEqual(len(consume(moving,counter)[0]),1)

    def test_lost_measurement_side_cannot_borrow_other_side_mid_cycle(self):
        frames=sequence();frames[11]['framing']['landmarks'][15]['valid']=False
        self.assertEqual(consume(frames)[0],[])


if __name__=='__main__':unittest.main()
