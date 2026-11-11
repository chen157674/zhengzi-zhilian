"""分类器运行契约；这些模拟分数不是动作准确率证据。"""
import unittest
from unittest.mock import patch,Mock
import numpy as np
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.video_vlm import LABELS


class Head:
    classes_=np.array(['bodyweight_squat','unknown'])
    def __init__(self,scores):self.scores=scores
    def predict_proba(self,values):
        if values.shape!=(1,2736):raise ValueError('输入维度错误')
        return np.asarray([self.scores])


class SemanticModelTests(unittest.TestCase):
    def predictor(self,scores):
        info={'version':'test','block':'full','minimum_score':.7,'minimum_margin':.2}
        with patch('fitness_app.semantic_model.resources',return_value=(info,None)):
            model=SemanticPredictor(research=True)
        model.head=Head(scores)
        return model

    def classify(self,scores):
        model=self.predictor(scores)
        return model.from_features(np.ones(2560,np.float32),{'scores':{a or 'unknown':.01 for a in LABELS}})

    def test_background_is_not_forced_into_supported_class(self):
        result=self.classify([.1,.9]);self.assertIsNone(result['action_id']);self.assertEqual(result['reason'],'video_background')

    def test_uncertainty_does_not_become_confident_action(self):
        self.assertIsNone(self.classify([.6,.4])['action_id'])
        result=self.classify([.85,.15]);self.assertEqual(result['action_id'],'bodyweight_squat')
        self.assertFalse(result['release_gate_passed']);self.assertFalse(result['score_is_calibrated_probability'])

    def test_bad_model_output_is_an_error(self):
        with self.assertRaises(ValueError):self.classify([float('nan'),.1])

    def test_unavailable_model_fails_before_loading_gpu(self):
        with patch('fitness_app.semantic_model.resources',return_value=(None,'semantic_model_not_released')):
            with self.assertRaisesRegex(RuntimeError,'not_released'):SemanticPredictor()


    def test_idle_timeout_starts_after_cold_load_and_old_timer_cannot_unload_new_work(self):
        model=self.predictor([.85,.15]);model.info['encoder_policy']={'base_revision':'fixture'}
        video=Mock();model.video=video
        clock=[0.]
        video.load.side_effect=lambda:clock.__setitem__(0,90.)
        with patch('fitness_app.semantic_model.time.monotonic',side_effect=lambda:clock[0]),patch('fitness_app.semantic_model.threading.Timer') as timers:
            model.warm()
            self.assertEqual(model.last_use,90.)
            first_generation=model.idle_generation
            clock[0]=100.;model._arm_idle()
            model._unload_idle(first_generation)
            self.assertIs(model.video,video)
            self.assertIsNotNone(model.idle_timer)
            clock[0]=158.;model._unload_idle(model.idle_generation)
            self.assertIs(model.video,video)
            clock[0]=161.
            with patch('torch.cuda.empty_cache'):
                model._unload_idle(model.idle_generation)
            self.assertIsNone(model.video)

    def test_predict_refreshes_idle_clock_after_inference_instead_of_before(self):
        model=self.predictor([.85,.15]);model.info['block']='semantic'
        video=Mock();model.video=video;clock=[0.]
        def inference(*args,**kwargs):
            clock[0]=75.
            return {'_embedding':np.ones(2560,np.float32),'scores':{a or 'unknown':.01 for a in LABELS}}
        video.predict.side_effect=inference
        with patch('fitness_app.semantic_model.observations',return_value=({},None)),patch.object(model,'from_features',return_value={'action_id':'bodyweight_squat'}),             patch('fitness_app.semantic_model.time.monotonic',side_effect=lambda:clock[0]),patch('fitness_app.semantic_model.threading.Timer'),patch('torch.cuda.empty_cache'):
            result=model.predict([],np.array([0,3000]),{})
        self.assertEqual(result['action_id'],'bodyweight_squat');self.assertEqual(model.last_use,75.)


    def test_confirmed_early_cross_review_skips_redundant_chest_family_inference(self):
        model=self.predictor([.85,.15]);model.info.update(block='semantic',pull_press_review={'execution_version':'early1'},
            refinement_enabled=True,refinement_families=['chest'])
        model.video=Mock();model.video.predict.return_value={'_embedding':np.ones(2560,np.float32)}
        primary={'action_id':'machine_chest_press','score':.99}
        reviewed={'action_id':'lat_pulldown','cross_review':{'version':'fixture'}}
        with patch('fitness_app.semantic_model.observations',return_value=({},None)),             patch.object(model,'from_features',return_value=primary),patch.object(model,'_cross_review',return_value=reviewed),             patch('fitness_app.video_refinement.choose_family',side_effect=AssertionError('不应重复胸部推理')),             patch('fitness_app.semantic_model.threading.Timer'),patch('torch.cuda.empty_cache'):
            value=model.predict([],np.array([0,3000]),{})
        self.assertEqual(value,reviewed)


    def test_fast_lane_uses_one_visual_pass_only_for_real_motion_and_strong_pulldown(self):
        model=self.predictor([.85,.15]);model.info['motion_evidence']={'config':{}}
        reviewer=Mock();reviewer.review.return_value={'action_id':'lat_pulldown','score':.99,'margin':.9}
        facts={'wrist_above_shoulder_max_torso':.8,'upper_arm_range_deg':80.,'elbow_range_deg':60.,'elbow_median_deg':100.}
        with patch.object(model,'_fast_model',return_value=reviewer),patch('fitness_app.semantic_model.motion_evaluate',return_value={'state':'moving','veto':False}):
            result=model._fast_decision([],np.array([0,2800]),{},facts)
            self.assertEqual(result['action_id'],'lat_pulldown')
            self.assertEqual(result['overhead_fast_review']['visual_forward_passes'],1)
            reviewer.review.assert_called_once()
            self.assertIsNone(model._fast_decision([],np.array([0,2800]),{'recognition_hint':'dumbbell_shoulder_press'},facts))
        reviewer.review.reset_mock()
        with patch.object(model,'_fast_model',return_value=reviewer),patch('fitness_app.semantic_model.motion_evaluate',return_value={'state':'insufficient','veto':False}):
            self.assertIsNone(model._fast_decision([],np.array([0,2800]),{},facts))
        reviewer.review.assert_not_called()

    def test_synthetic_fast_warmup_is_once_and_never_publishes_recognition(self):
        model=self.predictor([.85,.15]);model.info['overhead_fast_review']={'version':'fixture'}
        model.video=Mock();reviewer=Mock()
        with patch.object(model,'_fast_model',return_value=reviewer),patch('fitness_app.semantic_model.threading.Timer'):
            self.assertIsNone(model.warm());self.assertIsNone(model.warm())
        reviewer.review.assert_called_once();self.assertEqual(model.fast_warmup_forwards,1)
        frames,times=reviewer.review.call_args.args
        self.assertEqual(len(frames),8);self.assertEqual(frames[0].shape,(252,448,3))
        self.assertTrue(all(not frame.any() for frame in frames))
        self.assertEqual(times.tolist(),list(range(0,3200,400)))


if __name__=='__main__':unittest.main()
