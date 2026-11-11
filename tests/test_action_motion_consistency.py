import unittest,math
import numpy as np
from fitness_app.action_motion_consistency import extract,training_family,support_bounds,family_prediction,apply_consistency,policy_signature,VERSION
from fitness_app.motion_evidence import DEFAULT


class ActionMotionConsistencyTests(unittest.TestCase):
    def geometry(self,kind='curl'):
        n=32;p=np.full((n,33,5),np.nan);t=np.arange(n)*100
        for i in range(n):
            a=math.radians(5 if kind=='curl' else 15+80*i/(n-1));b=math.radians(50+100*i/(n-1) if kind=='curl' else 160)
            sh=np.array([.5,.25]);hip=np.array([.5,.65]);arm=np.array([math.sin(a),math.cos(a)])*.18;el=sh+arm;u=-arm/np.linalg.norm(arm)
            rotation=np.array([[math.cos(b),-math.sin(b)],[math.sin(b),math.cos(b)]]);wr=el+(rotation@u)*.16
            for j,xy in ((11,sh),(13,el),(15,wr),(23,hip)):p[i,j]=[*xy,0,1,1]
        return {'timestamps':t,'points':p,'sizes':np.tile([1000,1000],(n,1)),'people':np.ones(n,int)}
    def feature(self,*values):return {'state':'assessable','reason':'fixture','sides':[{'elbow_fraction':float(v)} for v in values]}
    def policy(self,accepted=True):
        p={'version':VERSION,'bounds':{'arm_elevation_max':.35,'elbow_flexion_min':.65},'calibration_accepted':accepted,'runtime_enabled':False};p['signature']=policy_signature(p);return p
    def test_same_side_geometry_distinguishes_motion_without_grip_or_error_labels(self):
        curl=extract(self.geometry('curl'),0,3100,DEFAULT);raise_=extract(self.geometry('raise'),0,3100,DEFAULT)
        self.assertEqual(curl['state'],'assessable');self.assertEqual(raise_['state'],'assessable')
        self.assertGreater(curl['sides'][0]['elbow_fraction'],.95);self.assertLess(raise_['sides'][0]['elbow_fraction'],.05)
        self.assertEqual(training_family({'label_scope':{'allowed_actions':['standing_dumbbell_curl','hammer_curl']}}),'elbow_flexion')
        self.assertIsNone(training_family({'label_scope':{'allowed_actions':['hammer_curl','dumbbell_front_raise']}}))
    def test_missing_hip_multi_person_or_stale_time_never_invents_geometry(self):
        for mode in ('hip','multiple','time'):
            a=self.geometry()
            if mode=='hip':a['points'][:,23,3]=0
            elif mode=='multiple':a['people'][3]=2
            else:a['timestamps'][3]=a['timestamps'][2]
            with self.subTest(mode=mode):self.assertEqual(extract(a,0,3100,DEFAULT)['state'],'insufficient')
    def test_training_envelopes_abstain_in_gap_on_overlap_or_disagreeing_sides(self):
        records=[{'family':'arm_elevation','geometry':self.feature(.2,.3)},{'family':'elbow_flexion','geometry':self.feature(.7,.9)}]
        bounds=support_bounds(records);self.assertEqual(bounds,{'arm_elevation_max':.3,'elbow_flexion_min':.7})
        self.assertEqual(support_bounds(records*3),bounds)
        self.assertIsNone(support_bounds(records+[{'family':'elbow_flexion','geometry':self.feature(.1)}]))
        for values in ((.5,),(.2,.9),(float('nan'),)):
            self.assertIsNone(family_prediction(self.feature(*values),self.policy()))
    def test_only_supported_cross_family_proposal_is_vetoed_and_unreleased_is_default_off(self):
        base={'action_id':'dumbbell_front_raise','score':.95,'model_version':'base'};candidate={'action_id':'hammer_curl','score':.99,'model_version':'candidate'};policy=self.policy()
        self.assertEqual(apply_consistency(base,candidate,self.feature(.2),policy),candidate)
        veto=apply_consistency(base,candidate,self.feature(.2),policy,research=True);self.assertEqual(veto['action_id'],'dumbbell_front_raise');self.assertEqual(veto['classification_consistency']['state'],'proposal_vetoed')
        supported=apply_consistency(base,candidate,self.feature(.9),policy,research=True);self.assertEqual(supported['action_id'],'hammer_curl')
        self.assertEqual(apply_consistency({'action_id':'standing_dumbbell_curl'},candidate,self.feature(.2),policy,research=True),candidate)
        self.assertEqual(apply_consistency({'action_id':None},candidate,self.feature(.2),policy,research=True),candidate)
    def test_unaccepted_insufficient_or_changed_policy_cannot_forge_a_veto(self):
        base={'action_id':'dumbbell_front_raise'};candidate={'action_id':'hammer_curl'}
        for features,policy in (({'state':'insufficient','sides':[]},self.policy()),(self.feature(.2),self.policy(False))):self.assertEqual(apply_consistency(base,candidate,features,policy,research=True)['action_id'],'hammer_curl')
        policy=self.policy();policy['bounds']['arm_elevation_max']=.4
        with self.assertRaises(ValueError):apply_consistency(base,candidate,self.feature(.2),policy,research=True)


if __name__=='__main__':unittest.main()
