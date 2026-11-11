"""适配分支只在明确范围/运动证据下改类；合成输入不证明真实准确率。"""
import unittest
import numpy as np
from fitness_app.head_adaptation import apply_adapted,route_needed,adapt_linear


class HeadAdaptationTests(unittest.TestCase):
    def moving(self):return {'state':'moving','veto':False}
    def test_unrelated_or_motion_rejected_unknown_cannot_be_rescued(self):
        for base in ({'action_id':'bodyweight_squat'},{'action_id':'cable_face_pull','motion_evidence':{'veto':True}},{'action_id':None,'top_action_id':'cable_chest_fly','score':.4,'motion_evidence':{'veto':True}},
                     {'action_id':None,'top_action_id':None,'score':.9}):
            self.assertFalse(route_needed(base));self.assertEqual(apply_adapted(base,'cable_face_pull',.99,.9,self.moving(),'candidate'),base)
    def test_low_margin_rescue_requires_positive_motion_not_just_no_veto(self):
        base={'action_id':None,'top_action_id':'cable_chest_fly','score':.46};self.assertTrue(route_needed(base))
        for evidence in ({'state':'insufficient','veto':False},{'state':'unconfirmed','veto':True}):
            self.assertEqual(apply_adapted(base,'cable_chest_fly',.95,.8,evidence,'candidate'),base)
        changed=apply_adapted(base,'cable_chest_fly',.95,.8,self.moving(),'candidate');self.assertEqual(changed['action_id'],'cable_chest_fly');self.assertFalse(changed['runtime_enabled'])
    def test_outside_proposal_or_bad_confidence_does_not_overwrite(self):
        base={'action_id':'cable_triceps_pushdown'}
        for action,score,margin in (('bodyweight_squat',.99,.9),('cable_face_pull',float('nan'),.9),('cable_face_pull',.79,.7),('cable_face_pull',.95,.24)):
            self.assertEqual(apply_adapted(base,action,score,margin,self.moving(),'candidate'),base)
    def test_adaptation_learns_allowed_targets_without_distilling_confident_wrong_teacher(self):
        x=np.asarray([[-2.],[-1.],[1.],[2.]],np.float32);mask=np.asarray([[1,0],[1,0],[0,1],[0,1]],bool);weights=np.ones(4,np.float32)
        teacher=np.asarray([[.99,.01]]*4,np.float32);initial=np.zeros((2,1),np.float32);bias=np.zeros(2,np.float32)
        result=adapt_linear(x,mask,weights,teacher,initial,bias,steps=200)
        self.assertEqual(result['protected_teacher_samples'],2);self.assertLess(result['history'][-1]['classification'],result['history'][0]['classification'])
        self.assertEqual((x@result['coef'].T+result['intercept']).argmax(1).tolist(),[0,0,1,1])


if __name__=='__main__':unittest.main()
