"""局部裁取边界/可见性与标签无关性；不是握法准确率真值。"""
import unittest
import numpy as np
from fitness_app.grip_features import local_patches,summarize,select_pose_frames
from fitness_app.action_label_policy import reviewed_scope,route

class GripInputTests(unittest.TestCase):
    def pose(self):
        p=np.full((33,5),np.nan,np.float32)
        for i,xy in [(11,(.3,.2)),(13,(.3,.5)),(15,(.5,.5))]:p[i]=[*xy,0.,.95,.95]
        return p
    def test_real_pixels_and_aspect_aware_arm_crop(self):
        rgb=np.zeros((200,400,3),np.uint8);rgb[:,:,0]=np.arange(400,dtype=np.uint8)
        rows=local_patches(rgb,self.pose());self.assertEqual(len(rows),1);self.assertAlmostEqual(rows[0]['elbow_angle'],90.)
        l,t,r,b=rows[0]['box'];self.assertTrue(0<=l<r<=400 and 0<=t<b<=200);np.testing.assert_array_equal(rows[0]['rgb'],rgb[t:b,l:r])
    def test_occluded_straight_or_degenerate_arm_never_invents_hand_patch(self):
        rgb=np.zeros((200,400,3),np.uint8)
        for what in ('hidden','straight','degenerate'):
            p=self.pose()
            if what=='hidden':p[15,3]=.1
            elif what=='straight':p[15,:2]=(.3,.8)
            else:p[15,:2]=p[13,:2]
            self.assertEqual(local_patches(rgb,p),[])
    def test_temporal_summary_uses_actual_vectors_and_rejects_missing_or_nonfinite(self):
        v=np.zeros((3,512),np.float32);v[:,0]=1
        summary=summarize(v);self.assertEqual(summary.shape,(1024,));self.assertAlmostEqual(summary[0],1.);self.assertFalse(summary[512:].any())
        for value in (v[:2],v*np.nan,np.zeros_like(v)):
            with self.assertRaises(ValueError):summarize(value)
    def test_nonfinite_candidate_scores_cannot_override_primary(self):
        primary={'action_id':'standing_dumbbell_curl','score':.9}
        for score in (float('nan'),float('inf'),-1.,1.01,True):self.assertEqual(route(primary,'hammer_curl',score,.9),primary)
    def test_review_is_bound_to_input_window_cache_and_source(self):
        entry={'id':'x','action_id':'standing_dumbbell_curl','source_sha256':'source','start_ms':0,'end_ms':5000,'crop':None,'partition':'train'}
        review={k:entry[k] for k in ('id','source_sha256','start_ms','end_ms','crop')}|{'semantic_cache_sha256':'cache','review_basis':'developer_visual_exact_frames','independent_error_truth':False,'decision':'verified_subtype','grip_label':'standing_dumbbell_curl','actual_timestamps_ms':[1000,2000,3000],'semantic_timestamps_ms':list(range(500,4500,500))}
        self.assertEqual(reviewed_scope(entry,review,'cache')['leaf_label'],'standing_dumbbell_curl')
        for change in ({'source_sha256':'other'},{'crop':[0,0,.5,.5]},{'actual_timestamps_ms':[0,1000,2000]},{'independent_error_truth':True}):
            with self.assertRaises(ValueError):reviewed_scope(entry,{**review,**change},'cache')
        with self.assertRaises(ValueError):reviewed_scope(entry,review,'changed-cache')
        self.assertIsNone(reviewed_scope(entry,{**review,'decision':'exclude_unconfirmed_exercise'},'cache')['leaf_label'])

    def test_semantic_cache_cannot_silently_change_source_window_or_encoder(self):
        import io,json,hashlib
        from fitness_app.paths import ROOT
        from scripts.train_curl_subtype import vector
        policy=json.loads((ROOT/'data/models/semantic-action-classifier.json').read_text(encoding='utf-8'))['encoder_policy_sha256']
        identity={'source_sha256':'source','start_ms':0,'end_ms':4000,'crop':None,'policy_sha256':policy}
        entry={k:identity[k] for k in ('source_sha256','start_ms','end_ms','crop')}
        def cache(value):
            f=io.BytesIO();signature=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
            np.savez_compressed(f,identity=json.dumps(value),signature=signature,embedding=np.ones((2560,),np.float32));f.seek(0);return f
        self.assertAlmostEqual(float(np.linalg.norm(vector(cache(identity),entry))),1.,places=6)
        for changed in ({**identity,'start_ms':100},{**identity,'crop':[0,0,.5,.5]},{**identity,'policy_sha256':'other'}):
            with self.assertRaises(ValueError):vector(cache(changed),entry)

    def sequence(self):
        n=32
        return {'timestamps':1000+np.arange(n)*100,'points':np.repeat(self.pose()[None],n,axis=0),
                'people':np.ones(n,np.int32),'sizes':np.tile([400,200],(n,1))}
    def test_pose_guided_bins_pick_visible_frames_within_original_window(self):
        data=self.sequence();data['points'][1,[11,13,15],3:]=.99
        times=select_pose_frames(data,1000,4100);self.assertEqual(len(times),8);self.assertEqual(times[0],1100)
        self.assertEqual(times,sorted(set(times)));self.assertTrue(all(1000<=t<=4100 for t in times))
    def test_pose_guided_sampling_never_fills_occluded_multiple_person_or_outside_frames(self):
        data=self.sequence();data['people'][:]=2;self.assertEqual(select_pose_frames(data,1000,4100),[])
        data['people'][:]=1;data['points'][:,15,3]=.1;self.assertEqual(select_pose_frames(data,1000,4100),[])
        self.assertEqual(select_pose_frames(self.sequence(),0,500),[])
    def test_sampling_is_label_independent_and_rejects_invalid_time_contract(self):
        data=self.sequence();expected=select_pose_frames(data,1000,4100)
        for label in ('hammer_curl','standing_dumbbell_curl','unknown'):
            self.assertEqual(select_pose_frames({**data,'expected_action':label},1000,4100),expected)
        with self.assertRaises(ValueError):select_pose_frames(data,1000,4100,9)
        data['timestamps'][2]=data['timestamps'][1]
        with self.assertRaises(ValueError):select_pose_frames(data,1000,4100)
    def test_group_weighting_does_not_reward_same_source_crops_or_more_originals(self):
        from fitness_app.action_label_policy import group_balanced_weights
        rows=[{'expected':'standing_dumbbell_curl','group':g,'source_sha256':h} for g,h in [('a','one'),('a','one'),('b','two'),('b','three')]]
        rows.append({'expected':'hammer_curl','group':'c','source_sha256':'four'})
        weights=group_balanced_weights(rows)
        self.assertAlmostEqual(sum(weights[:2]),sum(weights[2:4]));self.assertAlmostEqual(sum(weights[:4]),weights[4]);self.assertAlmostEqual(sum(weights),len(rows))
        self.assertAlmostEqual(weights[0],weights[1]);self.assertAlmostEqual(weights[2],weights[3])

if __name__=='__main__':unittest.main()
