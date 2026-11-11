"""二维共同特征和相机研究适配器；不把合成测试计为错误识别精度。"""
import copy,unittest
from types import SimpleNamespace
import numpy as np
from fitness_app.plank_transfer import frame_features,summarize,source_views,camera_points,classify,PlankTransferProbe,FEATURE_VERSION,PlankGeometryEnsemble,per_subject_metrics,choose_operating_point
from test_plank_hold import geometry

class PlankTransferTests(unittest.TestCase):
    def bundle(self):
        class Model:
            classes_=np.array([1,7,8])
            def predict_proba(self,x):return np.tile([.05,.9,.05],(len(x),1))
        return {'model':Model(),'threshold':.8,'feature_version':FEATURE_VERSION,'runtime_enabled':False}

    def frame(self,stamp):
        current=geometry(stamp//100);current['captured_ms']=stamp;current['identity_accepted']=True
        return current

    def test_person_constraint_rejects_good_average_that_hides_one_bad_subject(self):
        from fitness_app.ec3d_training import metrics
        rows=[{'subject':'A','label':1} for _ in range(10)]+[{'subject':'B','label':1} for _ in range(10)]+[{'subject':'A','label':7} for _ in range(10)]+[{'subject':'B','label':8} for _ in range(10)]
        unsafe=np.array([7]*3+[1]*17+[7]*10+[8]*10)
        safe=np.array([7]+[1]*19+[7]*8+[1]*2+[8]*8+[1]*2)
        options=[]
        for threshold,pred in ((.6,unsafe),(.8,safe)):
            score=metrics([r['label'] for r in rows],pred)
            options.append({'threshold':threshold,'metrics':score,'normal_false_positive_rate':score['error_detection']['fp']/20,
                'per_subject':per_subject_metrics(rows,pred)})
        chosen=choose_operating_point(options)
        self.assertEqual(chosen['threshold'],.8);self.assertTrue(chosen['subject_constraints_passed'])
        failed=choose_operating_point(options[:1]);self.assertFalse(failed['subject_constraints_passed'])

    def test_ensemble_averages_probabilities_and_preserves_label_order(self):
        from unittest.mock import patch
        class A:
            classes_=np.array([1,7,8])
            def predict_proba(self,x):return np.tile([.2,.7,.1],(len(x),1))
        class B(A):
            def predict_proba(self,x):return np.tile([.4,.2,.4],(len(x),1))
        ensemble=PlankGeometryEnsemble();ensemble.models=[A(),B()];ensemble.classes_=A.classes_
        np.testing.assert_allclose(ensemble.predict_proba(np.ones((2,48))),[[.3,.45,.25]]*2)
        ensemble.models=[]
        with self.assertRaises(ValueError):ensemble.predict_proba(np.ones((1,48)))

    def test_near_side_only_does_not_require_hidden_opposite_side(self):
        current=self.frame(0)
        for i in (11,13,15,23,25,27):current['framing']['landmarks'][i]['valid']=False
        self.assertIsNotNone(camera_points(current,'right'));self.assertIsNone(camera_points(current,'left'))
        current['framing']['landmarks'][14]['valid']=False
        self.assertIsNone(camera_points(current,'right'))

    def test_coordinate_translation_uniform_scale_and_horizontal_mirror_are_invariant(self):
        points=camera_points(self.frame(0),'right');sequence=np.repeat(points[None],8,axis=0)
        expected=summarize(sequence)
        np.testing.assert_allclose(expected,summarize(sequence*1.3+[50,-20]),atol=1e-12)
        mirrored=sequence.copy();mirrored[:,:,0]=1000-mirrored[:,:,0]
        np.testing.assert_allclose(expected,summarize(mirrored),atol=1e-12)

    def test_shared_mapping_uses_real_shoulder_elbow_wrist_hip_knee_ankle(self):
        points=camera_points(self.frame(0),'right');raw=np.zeros((8,3,25))
        for ids in ((2,3,4,9,10,11),(5,6,7,12,13,14)):
            for i,j in enumerate(ids):raw[:,2,j]=points[i,0];raw[:,1,j]=points[i,1]
        views=source_views(raw,yaws=(0.,))
        self.assertEqual(len(views),2)
        for v in views:np.testing.assert_allclose(v['feature'],summarize(np.repeat(points[None],8,axis=0)))

    def test_current_visibility_support_or_view_loss_immediately_withdraws(self):
        for mode in ('missing','unsupported','front','identity'):
            probe=PlankTransferProbe(self.bundle())
            for stamp in range(0,2000,200):result=probe.update(self.frame(stamp),side='right',view='side',forearm_supported=True)
            self.assertEqual(result['status'],'candidate');self.assertFalse(result['publishable'])
            current=self.frame(2000)
            if mode=='missing':current['framing']['landmarks'][24]['valid']=False
            if mode=='identity':current['identity_accepted']=False
            result=probe.update(current,side='right',view='front' if mode=='front' else 'side',forearm_supported=mode!='unsupported')
            self.assertEqual(result['status'],'unassessable');self.assertFalse(result['publishable'])
            result=probe.update(self.frame(2200),side='right',view='side',forearm_supported=True)
            self.assertEqual(result['reason'],'awaiting_continuous_window')

    def test_four_real_observations_and_1200ms_required_even_at_low_fps(self):
        probe=PlankTransferProbe(self.bundle())
        for stamp in (0,400,800):self.assertEqual(probe.update(self.frame(stamp),side='right',view='side',forearm_supported=True)['status'],'unassessable')
        result=probe.update(self.frame(1200),side='right',view='side',forearm_supported=True)
        self.assertEqual(result['status'],'unassessable');self.assertEqual(result['observed_frames'],4)
        self.assertEqual(result['candidate_label'],7);self.assertIsNone(result['label'])
        self.assertEqual(result['source_window_ms'],[0,1200]);self.assertFalse(result['runtime_enabled'])
        probe.update(self.frame(1600),side='right',view='side',forearm_supported=True)
        result=probe.update(self.frame(2000),side='right',view='side',forearm_supported=True)
        self.assertEqual(result['status'],'candidate');self.assertEqual(result['confirmation_observations'],3)

    def test_gap_old_timestamp_and_side_change_collect_fresh_window(self):
        for mode in ('gap','old','side'):
            probe=PlankTransferProbe(self.bundle())
            for stamp in range(0,1600,200):probe.update(self.frame(stamp),side='right',view='side',forearm_supported=True)
            stamp=2200 if mode=='gap' else 1200 if mode=='old' else 1600
            result=probe.update(self.frame(stamp),side='left' if mode=='side' else 'right',view='side',forearm_supported=True)
            self.assertEqual(result['reason'],'awaiting_continuous_window')

    def test_single_abnormal_frame_is_unknown_and_cannot_confirm_after_neutral(self):
        bundle=self.bundle()
        class Flipping:
            classes_=np.array([1,7,8])
            values=[1,7,7,1,7,7,7]
            def predict_proba(self,x):
                label=self.values.pop(0)
                return np.array([[.95,.025,.025] if label==1 else [.05,.9,.05]])
        bundle['model']=Flipping();probe=PlankTransferProbe(bundle)
        results=[]
        for stamp in range(0,2600,200):results.append(probe.update(self.frame(stamp),side='right',view='side',forearm_supported=True))
        known=[r for r in results if r.get('candidate_label') is not None]
        self.assertEqual([r['status'] for r in known],['candidate','unassessable','unassessable','candidate','unassessable','unassessable','candidate'])
        self.assertTrue(all(r['publishable'] is False for r in known))

    def test_bad_probabilities_or_production_model_are_rejected(self):
        for p in ([[.1,.1,.1]],[[-.1,.5,.6]],[[np.nan,0,1]],[[1,0,2]]):
            with self.assertRaises(ValueError):classify(p,[1,7,8],.8)
        with self.assertRaises(ValueError):PlankTransferProbe({**self.bundle(),'runtime_enabled':True})

    def test_degenerate_or_upright_geometry_is_not_accepted_as_plank(self):
        for p in (np.zeros((4,6,2)),np.full((4,6,2),np.nan),np.ones((4,5,2))):
            with self.assertRaises(ValueError):frame_features(p)
        points=camera_points(self.frame(0),'right');upright=points[:,::-1]
        with self.assertRaises(ValueError):frame_features(np.repeat(upright[None],4,axis=0))

if __name__=='__main__':unittest.main()
