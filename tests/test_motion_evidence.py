"""运动保护的物理/缺失边界；合成坐标只验证工程逻辑，不是分类准确率。"""
import unittest
import numpy as np
from fitness_app.motion_evidence import evaluate,apply


def sample():
    t=np.arange(0,3201,100);p=np.zeros((len(t),33,5));p[:,:,0]=.5;p[:,:,1]=.5;p[:,:,3:]=.99
    for index,x,y in [(11,.4,.2),(12,.6,.2),(13,.4,.4),(14,.6,.4),(15,.4,.6),(16,.6,.6),(23,.45,.6),(24,.55,.6),(25,.45,.75),(26,.55,.75),(27,.45,.95),(28,.55,.95)]:p[:,index,0]=x;p[:,index,1]=y
    return {'timestamps':t,'points':p,'sizes':np.tile([640,480],(len(t),1)),'people':np.ones(len(t),int)}


class MotionTests(unittest.TestCase):
    def test_static_preparation_veto_keeps_raw_candidate_not_a_false_exercise(self):
        a=sample();r=evaluate(a,0,3200,'barbell_bent_over_row');self.assertTrue(r['veto']);self.assertEqual(r['state'],'unconfirmed')
        decision={'action_id':'barbell_bent_over_row','candidate_action_id':'barbell_bent_over_row','top_action_id':'barbell_bent_over_row','score':.99}
        guarded=apply(decision,r);self.assertIsNone(guarded['action_id']);self.assertEqual(guarded['top_action_id'],'barbell_bent_over_row');self.assertEqual(guarded['score'],.99)
    def test_one_arm_partial_motion_can_support_activity_without_requiring_a_full_rep(self):
        a=sample();a['points'][:,15,0]=.4+.15*np.sin(np.linspace(0,np.pi,len(a['timestamps'])))
        r=evaluate(a,0,3200,'hammer_curl');self.assertFalse(r['veto']);self.assertEqual(r['state'],'moving')
    def test_static_plank_is_explicitly_exempt(self):
        r=evaluate(sample(),0,3200,'forearm_plank');self.assertFalse(r['veto']);self.assertEqual(r['state'],'static_exempt')
    def test_missing_arms_multiple_people_and_gaps_are_not_treated_as_static(self):
        a=sample();a['points'][:,[13,14,15,16],:]=np.nan;self.assertFalse(evaluate(a,0,3200,'hammer_curl')['veto'])
        a=sample();a['people'][5]=2;self.assertEqual(evaluate(a,0,3200,'hammer_curl')['state'],'insufficient')
        a=sample();a['timestamps'][15:]+=600;self.assertEqual(evaluate(a,0,4000,'hammer_curl')['reason'],'motion_source_gap')
    def test_camera_translation_does_not_create_arm_motion(self):
        a=sample();a['points'][:,:,:2]+=np.linspace(0,.05,len(a['timestamps']))[:,None,None]
        self.assertTrue(evaluate(a,0,3200,'barbell_bent_over_row')['veto'])
    def test_leg_only_motion_is_supported_and_low_confidence_is_not_a_zero_measurement(self):
        a=sample();a['points'][:,25,0]+=.12*np.sin(np.linspace(0,np.pi,len(a['timestamps'])))
        r=evaluate(a,0,3200,'high_knees');self.assertEqual(r['state'],'moving');self.assertFalse(r['veto'])
        a['points'][:,:,3:]=.1;r=evaluate(a,0,3200,'high_knees');self.assertEqual(r['state'],'insufficient');self.assertFalse(r['veto'])

    def test_bridge_top_hold_is_not_the_same_as_supine_preparation(self):
        a=sample()
        for side in (0,1):
            a['points'][:,11+side,:2]=(.2,.65);a['points'][:,23+side,:2]=(.45,.45)
            a['points'][:,25+side,:2]=(.65,.32);a['points'][:,27+side,:2]=(.70,.75)
        r=evaluate(a,0,3200,'bodyweight_glute_bridge');self.assertEqual(r['state'],'hold_supported');self.assertFalse(r['veto'])
        for side in (0,1):a['points'][:,23+side,:2]=(.45,.70)
        r=evaluate(a,0,3200,'bodyweight_glute_bridge');self.assertTrue(r['veto'])


if __name__=='__main__':unittest.main()
