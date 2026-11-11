"""普通弯举的连续纠错、周期幅度与保存闭环；合成几何只验证工程行为。"""
import copy
import math
import unittest

from fitness_app.catalogue import get_action
from fitness_app.coaching import coaching_observation, coaching_snapshot, committed_issues
from fitness_app.posture_engine import PostureEngine, create_analysis_engine
from fitness_app.posture_rules import rules_for
from fitness_app.skeleton import REGIONS, skeleton_snapshot
from test_action_counting import geometry
from test_posture_backend import EventSource
from test_quality_candidates import fixture
import test_formal_records as shared


ACTION='standing_dumbbell_curl'
STATIC={'curl.trunk_sway','curl.upper_arm_swing'}


def sequence(*, arm_swing=False, trunk_sway=False):
    frames=[]
    for index in range(42):
        frame=geometry(ACTION,115+45*math.cos(index*math.pi/10),seq=index)
        frame['captured_ms']=index*100
        frame['framing'].update(usable=True,code='VISIBLE')
        points=frame['framing']['landmarks']
        for offset in (0,1):
            if trunk_sway:
                delta=.22*math.sin(index*math.pi/10)
                for joint in (11,13,15):points[joint+offset]['x']+=delta
            if arm_swing:
                shoulder=points[11+offset]
                angle=math.radians(35+30*math.sin(index*math.pi/10))
                old=points[13+offset]
                x=shoulder['x']+.2*math.sin(angle)
                y=shoulder['y']+.2*math.cos(angle)
                points[15+offset]['x']+=x-old['x']
                points[15+offset]['y']+=y-old['y']
                points[13+offset].update(x=x,y=y)
        frames.append(frame)
    return frames


def consume(frames):
    engine=create_analysis_engine(ACTION)
    results=[engine.process(frame,{'coaching_view':'side','stream_epoch':'curl'}) for frame in frames]
    return engine,results


class CurlCorrectionTests(unittest.TestCase):
    def test_stable_curl_and_both_new_deviations(self):
        expected={
            (False,False):set(),
            (True,False):{'curl.upper_arm_swing'},
            (False,True):{'curl.trunk_sway'},
        }
        for (arm,trunk),issues in expected.items():
            with self.subTest(arm=arm,trunk=trunk):
                engine,results=consume(sequence(arm_swing=arm,trunk_sway=trunk))
                items={item['code']:item for item in results[-1]['observation']['posture']['items']}
                self.assertEqual({code for code in STATIC if items[code]['status']=='issue'},issues)
                self.assertEqual(set(REGIONS)&{r['code'] for r in engine.rules},{'curl.range',*STATIC})
                for code in issues:
                    self.assertTrue(items[code]['suggestion'])
                    self.assertTrue(items[code]['body_parts'])
                    cue={'state':'needs_adjustment','primary_cue':{**items[code],'cue_id':'curl-fixture'}}
                    self.assertTrue(skeleton_snapshot(sequence()[-1],cue)['highlight'])

    def test_short_input_wrong_view_and_hidden_parts_do_not_report_normal(self):
        frames=sequence(arm_swing=True)
        _,results=consume(frames[:6])
        self.assertTrue(all(i['status']=='unassessable' for i in results[-1]['observation']['posture']['items']))
        for mode in ('hidden','front','multi'):
            with self.subTest(mode=mode):
                engine=create_analysis_engine(ACTION)
                current=copy.deepcopy(frames)
                for frame in current:
                    if mode=='hidden':
                        for index in (15,16):frame['framing']['landmarks'][index]['valid']=False
                    if mode=='multi':frame['framing']['person_count']=2
                    result=engine.process(frame,{'stream_epoch':'curl','coaching_view':'front' if mode=='front' else 'side'})
                self.assertTrue(all(i['status']=='unassessable' for i in result['observation']['posture']['items']))

    def test_cycle_does_not_duplicate_or_replace_static_findings(self):
        frames,event,_=fixture(ACTION,True)
        engine=PostureEngine(ACTION,EventSource(event))
        for frame in frames:result=engine.process(frame,{'coaching_view':'oblique','stream_epoch':'curl'})
        self.assertEqual([a['code'] for a in result['events'][0]['assessments']],['curl.range'])
        observed=coaching_observation(result)
        items=observed['posture']['items']
        self.assertEqual({i['code'] for i in items},{'curl.range',*STATIC})
        self.assertEqual(next(i for i in items if i['code']=='curl.range')['side'],'left')
        self.assertNotIn('side',result['events'][0]['assessments'][0])

    def test_current_occlusion_withdraws_cue_and_highlight(self):
        frames=sequence(arm_swing=True)
        engine,results=consume(frames[:-1])
        previous=committed_issues(results[-1])
        self.assertTrue(any(i['code']=='curl.upper_arm_swing' for i in previous))
        current=copy.deepcopy(frames[-1])
        for index in (15,16):current['framing']['landmarks'][index]['valid']=False
        result=engine.process(current,{'stream_epoch':'curl','coaching_view':'side'})
        current['analysis']=coaching_observation(result)
        cue=coaching_snapshot({'status':'running','action_id':ACTION,'supported_items':engine.rules},current,previous,100)
        self.assertIsNone(cue['primary_cue'])
        self.assertIsNone(skeleton_snapshot(current,cue)['highlight'])

    def test_occlusion_recovery_collects_fresh_continuous_evidence(self):
        frames=sequence(arm_swing=True)
        for index in (15,16):frames[25]['framing']['landmarks'][index]['valid']=False
        _,outputs=consume(frames)
        before={i['code']:i for i in outputs[24]['observation']['posture']['items']}
        self.assertEqual(before['curl.upper_arm_swing']['status'],'issue')
        for result in outputs[25:38]:
            self.assertTrue(all(i['status']=='unassessable' for i in result['observation']['posture']['items'] if i['code'] in STATIC))
        recovered={i['code']:i for i in outputs[-1]['observation']['posture']['items']}
        self.assertEqual(recovered['curl.upper_arm_swing']['status'],'issue')

    def test_saved_findings_and_http_contract_keep_owner_and_counts(self):
        fx=shared.RecordTests();fx.setUp()
        try:
            sid=fx.running(ACTION)
            _,results=consume(sequence(arm_swing=True))
            findings=results[-1]['posture_findings']
            fx.store.append_posture(fx.user,sid,1,findings,fx.auth,4100)
            saved=fx.store.snapshot(fx.user,sid,True)
            self.assertEqual(saved['total_count'],0)
            self.assertTrue(any(i['code']=='curl.upper_arm_swing' and i['status']=='issue' for i in saved['posture_observations']))
            response=fx.client.get('/api/v1/records/'+sid+'/posture')
            self.assertEqual(response.status_code,200)
            other=fx.app.test_client();fx.login(other,'Other_User')
            self.assertEqual(other.get('/api/v1/records/'+sid+'/posture').status_code,404)
            self.assertEqual({i['code'] for i in get_action(ACTION)['issues']},{'curl.range',*STATIC})
        finally:fx.tearDown()


if __name__=='__main__':unittest.main()
