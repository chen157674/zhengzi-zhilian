"""时序补充模型只在画面一致时补充分类；不绕过未知、遮挡与变式门槛。"""
import copy,unittest
import numpy as np
from fitness_app.temporal_gym import sequence,agreement,TemporalGym
from fitness_app.automatic_recognition import body_frame
from test_gym_actions import raise_sequence


class TemporalGymTests(unittest.TestCase):
    def frames(self):
        frames=raise_sequence()
        for f in frames:f['world_landmarks']=[{'x':p['x'],'y':p['y'],'z':p['z']} for p in f['framing']['landmarks']]
        return frames

    def test_short_window_features_only_resample_without_mutating_joints(self):
        frames=self.frames()[:16];before=copy.deepcopy(frames)
        values,reason=sequence(frames)
        self.assertIsNone(reason);self.assertEqual(values.shape,(1,32,39));self.assertEqual(frames,before)

    def test_missing_world_core_target_and_gap_reject(self):
        for kind in ('world','core','people','gap','stale'):
            frames=self.frames()
            for f in frames:
                if kind=='world':f['world_landmarks']=[]
                if kind=='core':
                    for i in (11,12,23,24):f['framing']['landmarks'][i]['valid']=False
                if kind=='people':f['framing']['person_count']=2
                if kind=='gap':f['captured_ms']*=10
                if kind=='stale':f['captured_ms']=0
            self.assertIsNone(sequence(frames)[0],kind)

    def test_unsupported_chest_press_cannot_be_replaced_with_pec_deck(self):
        temporal={'label':'chest fly machine','score':.99,'margin':.95,'reason':None}
        self.assertIsNone(agreement(temporal,{'machine_chest_press':.95,'pec_deck_fly':.04}))
        self.assertIsNone(agreement({'label':'bench press','score':.99,'margin':.9},{'barbell_bench_press':.49,'dumbbell_bench_press':.48}))

    def test_two_models_agree_without_normalizing_uncertainty_into_certainty(self):
        temporal={'label':'bench press','score':.99,'margin':.9,'reason':None}
        result=agreement(temporal,{'barbell_bench_press':.68,'dumbbell_bench_press':.15,'pec_deck_fly':.1})
        self.assertEqual(result['candidate_action_id'],'barbell_bench_press');self.assertFalse(result['joint_score_is_probability'])
        self.assertIsNone(agreement(temporal,{'barbell_bench_press':.1,'dumbbell_bench_press':.01}))

    def test_body_frame_never_pairs_world_from_multiple_people(self):
        points=self.frames()[0]['framing']['landmarks']
        self.assertEqual(body_frame({'poses':[points,points],'world_poses':[points]},100,800,800,1)['world_landmarks'],[])
        self.assertEqual(body_frame({'poses':[points],'world_poses':[points]},100,800,800,1)['world_landmarks'],points)

    def test_resource_is_lazy_and_input_missing_never_loads_runtime(self):
        model=TemporalGym();self.assertIsNone(model.session)
        self.assertIsNone(model.predict([]).get('label'));self.assertIsNone(model.session)


if __name__=='__main__':unittest.main()
