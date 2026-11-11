"""13 新资源入口契约；复用真实预测，不训练、推理或重测指标公式。"""
from pathlib import Path
import unittest
from unittest.mock import patch

from fitness_app.auth import APIError
from fitness_app.experiment_sources import ExperimentSources,model_state


class ExperimentSourceTests(unittest.TestCase):
    def setUp(self):self.sources=ExperimentSources()

    def payload(self):
        task=self.sources.options()['tasks'][0]
        return {'task_id':task['task_id'],'dataset_version':task['dataset_version'],'split_hash':task['split_hash'],'method_ids':['majority','svm-c0.1']}

    def test_actual_tasks_snapshot_zero_null_and_truth_scope(self):
        options=self.sources.options();self.assertEqual(options['status'],'ready')
        self.assertEqual({task['task_id']:task['samples'] for task in options['tasks']},{'EXP-02':40,'EXP-03A':22,'EXP-03B':24,'EXP-03C':22})
        self.assertTrue(all(sum(method['ready'] for method in task['methods'])==3 for task in options['tasks']))
        snapshot=self.sources.snapshot(self.payload());self.assertEqual(snapshot['config']['evaluation_mode'],'verified_prediction_cache')
        self.assertEqual(snapshot['config']['primary_status'],'development_targets_unmet')
        metrics=snapshot['methods']['majority']['metrics']['main'];self.assertIsNone(metrics['positive_precision']);self.assertEqual(metrics['positive_recall'],0)
        self.assertIn('cycle_count_accuracy',snapshot['config']['unavailable_metrics'])
        snapshot['methods']['majority']['predictions'].clear()
        self.assertEqual(len(self.sources.snapshot(self.payload())['methods']['majority']['predictions']),40,'执行快照互不污染')

    def test_allowlist_resource_failure_and_no_implicit_replacement(self):
        payload=self.payload()
        for methods in (['majority'],['majority','majority'],['majority','geometry-rule'],['majority','C:/script.py']):
            with self.assertRaises(APIError):self.sources.snapshot({**payload,'method_ids':methods})
        with self.assertRaises(APIError):self.sources.snapshot({**payload,'script_path':'arbitrary'})
        with self.assertRaises(APIError) as caught:self.sources.snapshot({**payload,'split_hash':'different'})
        self.assertEqual(caught.exception.code,'STALE_EXPERIMENT_VERSION')
        def missing_svm(item):return (False,'D-FAULT missing model') if item.get('family')=='svm' else model_state(item)
        with patch('fitness_app.experiment_sources.model_state',side_effect=missing_svm):
            task=self.sources.options()['tasks'][0];self.assertTrue(task['ready'])
            with self.assertRaises(APIError) as caught:self.sources.snapshot(payload)
            self.assertEqual(caught.exception.code,'METHOD_UNAVAILABLE')
        original=Path.read_bytes
        def changed(path):return original(path)+(b' ' if path.name=='test-results.json' else b'')
        with patch.object(Path,'read_bytes',changed):
            unavailable=ExperimentSources().options();self.assertEqual(unavailable['status'],'unavailable');self.assertEqual(unavailable['tasks'],[])


if __name__=='__main__':unittest.main()
