"""本机已下载的真实权重→鉴权接口→反馈保存；原数据重放不作独立准确率。"""
import csv
import json
import os
import unittest
from unittest.mock import patch
from fitness_app.paths import ROOT
from fitness_app.pretrained_resources import MANIFEST,repo_asset,registry,checked
from fitness_app.exercise_feedback import ACTIONS,ExerciseFeedbackModel
from fitness_app.posture_rules import policy_version
import test_formal_records as fixtures


@unittest.skipUnless(MANIFEST.is_file(),'需先准备本机预训练资源')
class PretrainedResourceTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'FITNESS_PRETRAINED_CORRECTION':'1'});self.env.start();policy_version.cache_clear()
        self.fx=fixtures.RecordTests();self.fx.config_overrides={'PRETRAINED_CORRECTION_ENABLED':True};self.fx.setUp()

    def tearDown(self):
        try:
            for hook in self.fx.app.extensions.get('stop_hooks',[]):hook()
            self.fx.tearDown()
        finally:self.env.stop();policy_version.cache_clear()

    def original_points(self,action):
        from fitness_app.frame_pipeline import analysis_framing
        path,_=repo_asset('exercise-pose-feedback',f'Afterprocessing/{ACTIONS[action]}_coords.csv')
        model=ExerciseFeedbackModel(action)
        with path.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);columns=[key for key in reader.fieldnames if key!='class']
            for row in reader:
                values=[float(row[key]) for key in columns]
                points=[dict(zip(('x','y','z','visibility'),values[i:i+4])) for i in range(0,132,4)]
                for point in points:point['presence']=point['visibility']
                result=model.observe(points)
                if result.get('confidence',0)>=.8 and '_correct_' not in result['label'] and analysis_framing(action,{'poses':[points]})['usable']:
                    return points,result
        self.fail('原CSV中没有完整可见的偏差候选')

    def test_real_tree_models_through_authenticated_api(self):
        for action in ACTIONS:
            with self.subTest(action=action):
                points,expected=self.original_points(action)
                response=self.fx.post(self.fx.client,'/api/v1/correction-models/pose-check',{'action_id':action,'landmarks':points})
                self.assertEqual(response.status_code,200,response.json)
                result=response.json['data'];self.assertEqual(result['label'],expected['label'])
                self.assertEqual(result['model_sha256'],expected['model_sha256'])
                self.assertAlmostEqual(sum(result['class_probabilities'].values()),1.)
        self.assertEqual(self.fx.client.get('/api/v1/records').json['data']['total'],0)

    def test_original_h5_weights_compare_original_kaggle_poses_through_api(self):
        import torch
        from fitness_app.reference_similarity import ReferenceSimilarity
        threads=torch.get_num_threads();torch.set_num_threads(2)
        try:
            model=ReferenceSimilarity();dataset=registry()['datasets']['sitrex'];sequences=[]
            for item in dataset['files']:
                if '/bench press_json/' not in item['upstream_path']:continue
                original=json.loads(checked(item['path'],item).read_text(encoding='utf-8'))
                sequences.append(model.angles(original['frames'],normalized=True).tolist())
            self.assertEqual(len(sequences),2)
            payload={'action_id':'barbell_bench_press','reference_action_id':'barbell_bench_press',
                     'reference_angles':sequences[0],'observed_angles':sequences[1]}
            response=self.fx.post(self.fx.client,'/api/v1/correction-models/reference-compare',payload)
            self.assertEqual(response.status_code,200,response.json);result=response.json['data']
            self.assertEqual(len(result['angles']),23);self.assertIsNone(result['correctness'])
            self.assertTrue(all(0<=a[key]<=1 for a in result['angles'] for key in ('usefulness','similarity')))
            self.assertEqual(result['model_sha256'],model.similarity_resource['sha256'])
            self.assertEqual(self.fx.client.get('/api/v1/records').json['data']['total'],0)
        finally:torch.set_num_threads(threads)

    def test_original_tree_observation_reaches_engine_and_saved_record(self):
        from fitness_app.frame_pipeline import analysis_framing
        from fitness_app.posture_engine import create_analysis_engine
        for action in ACTIONS:
            with self.subTest(action=action):
                points,expected=self.original_points(action);sid=self.fx.running(action);engine=create_analysis_engine(action)
                for stamp in range(0,1300,100):
                    frame={'frame_seq':stamp//100,'captured_ms':stamp,'width':1280,'height':720,
                           'framing':analysis_framing(action,{'poses':[points]}),
                           'pretrained':{'status':'observed','timestamp_ms':stamp,'source_landmarks':points}}
                    output=engine.process(frame,{'coaching_view':'auto','stream_epoch':'original-csv-replay'})
                    if output.get('posture_findings'):
                        result=self.fx.writer.submit(self.fx.user,sid,self.fx.auth,'posture',
                            {'set_index':1,'findings':output['posture_findings'],'active_ms':stamp})
                        self.assertEqual(result['save_state'],'clean')
                saved=self.fx.store.snapshot(self.fx.user,sid,True)['posture_observations']
                candidate=[p for p in saved if p['code']==action+'.pretrained_posture' and p['status']=='issue']
                self.assertTrue(candidate)
                self.assertEqual(candidate[-1]['evidence']['label'],expected['label'])
                self.assertEqual(candidate[-1]['evidence']['model_sha256'],expected['model_sha256'])
                self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['total_count'],0)
                self.fx.finish(sid)


if __name__=='__main__':unittest.main()
