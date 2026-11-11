"""运动策略资源身份、真实运行入口与精炼不可绕过；协议替身不作准确率。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib,json,threading,unittest
from unittest.mock import patch,Mock
import numpy as np
from fitness_app.paths import ROOT
from fitness_app.motion_evidence import DEFAULT,VERSION,policy_signature,read_policy,MotionPolicyError
from fitness_app.semantic_model import SemanticPredictor,resources
from fitness_app.semantic_recognition import create_recognizer
from fitness_app.video_vlm import LABELS
import test_motion_evidence as motion


class Head:
    classes_=np.array(['barbell_bent_over_row','unknown'])
    def predict_proba(self,values):return np.asarray([[.99,.01]])


class MotionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.folder=TemporaryDirectory(dir=ROOT/'.tmp/tests',prefix='motion-policy-');self.path=Path(self.folder.name)
        info=json.loads((ROOT/'data/models/semantic-action-classifier.json').read_text('utf8'));self.base=info.get('base_version',info['version'])
        self.config={**DEFAULT,'angle_degrees':5.,'displacement_torso':.06};signature=policy_signature(self.config)
        evaluation={'base_model_version':self.base,'policy_signature':signature,'selected':{'config':self.config},'safe_nonregression_candidate':True,'unreviewed_correct_vetoes':[]}
        self.evaluation=self.path/'evaluation.json';self.evaluation.write_text(json.dumps(evaluation),encoding='utf8')
        self.info={**info,'base_version':self.base,'version':self.base+'-motion-'+signature[:10],'motion_evidence':{'version':VERSION,'config':self.config,'signature':signature,
            'base_weight_sha256':info['sha256'],'evaluation_path':self.evaluation.relative_to(ROOT).as_posix(),'evaluation_sha256':hashlib.sha256(self.evaluation.read_bytes()).hexdigest()}}
    def tearDown(self):self.folder.cleanup()

    def test_policy_identity_and_changed_evaluation_are_checked(self):
        self.assertEqual(read_policy(self.info)['config'],self.config)
        self.evaluation.write_text('{}',encoding='utf8')
        with self.assertRaises(MotionPolicyError):read_policy(self.info)

    def test_invalid_configuration_and_external_paths_are_rejected(self):
        for key,value in [('angle_degrees',True),('minimum_frames',2),('minimum_coverage',1.1)]:
            info={**self.info,'motion_evidence':{**self.info['motion_evidence'],'config':{**self.config,key:value}}}
            with self.assertRaises(MotionPolicyError):read_policy(info)
        info={**self.info,'motion_evidence':{**self.info['motion_evidence'],'evaluation_path':'../outside.json'}}
        with self.assertRaises(MotionPolicyError):read_policy(info)

    def test_invalid_motion_resource_cannot_silently_downgrade_to_unguarded_classifier(self):
        info={**self.info,'motion_evidence':{**self.info['motion_evidence'],'signature':'bad'}};manifest=self.path/'manifest.json';manifest.write_text(json.dumps(info),encoding='utf8')
        with patch('fitness_app.semantic_model.MANIFEST',manifest):
            self.assertEqual(resources()[1],'motion_evidence_policy_unavailable')
            fallback=Mock();recognizer=create_recognizer(fallback)
            self.assertFalse(recognizer.snapshot()['available']);fallback.assert_not_called();self.assertIsNone(recognizer.thread);recognizer.close()

    def test_video_runtime_and_family_review_both_apply_motion_guard(self):
        predictor=object.__new__(SemanticPredictor);predictor.info={**self.info,'block':'full','minimum_score':.35,'minimum_margin':.05,'refinement_enabled':False}
        predictor.lock=threading.Lock();predictor.head=Head();predictor._arm_idle=lambda:None;predictor.refiner=None
        video=Mock();video.model=None;video.predict.return_value={'_embedding':np.ones(2560,np.float32),'scores':{a or 'unknown':.01 for a in LABELS},'pose_observations':{}}
        predictor.video=video;predictor._video=lambda:video
        arrays=motion.sample();timestamps=arrays['timestamps'][np.linspace(0,len(arrays['timestamps'])-1,8).round().astype(int)];images=[np.zeros((48,64,3),np.uint8)]*8
        raw=predictor.from_features(np.ones(2560,np.float32),{'scores':{a or 'unknown':.01 for a in LABELS}})
        self.assertEqual(raw['model_version'],self.base);self.assertFalse(raw['motion_evidence_applied'])
        result=predictor.predict(images,timestamps,arrays);self.assertIsNone(result['action_id']);self.assertTrue(result['motion_evidence_applied']);self.assertEqual(result['model_version'],self.info['version'])
        predictor.info.update(refinement_enabled=True,refinement_families=['hinge']);predictor.refiner=Mock()
        predictor.refiner.review.return_value={'action_id':'barbell_bent_over_row','score':.99,'margin':.9,'top_action_id':'barbell_bent_over_row'}
        video.predict.return_value={'_embedding':np.ones(2560,np.float32),'scores':{a or 'unknown':.01 for a in LABELS},'pose_observations':{}}
        with patch('fitness_app.video_refinement.choose_family',return_value='hinge'):
            result=predictor.predict(images,timestamps,arrays)
        self.assertEqual(result['refinement_action_id'],'barbell_bent_over_row');self.assertIsNone(result['action_id']);self.assertEqual(result['reason'],'motion_unconfirmed')


if __name__=='__main__':unittest.main()
