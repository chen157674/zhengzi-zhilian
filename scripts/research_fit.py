"""只运行开发比较；独立测试须另经冻结门禁。"""
import json
import sys
import argparse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import OUTPUT
from fitness_app.research_training import development_comparison
from fitness_app.database import maintenance_lock
from fitness_app.paths import runtime_path
from fitness_app.research_freeze import freeze,independent_test


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=('compare','freeze','test'),default='compare');args=parser.parse_args()
    with maintenance_lock(runtime_path()/'service.lock',exact=True):
        if args.phase=='freeze':
            frozen=freeze();print(json.dumps({'status':'frozen','actions':len(frozen['actions']),'test_used':False}));raise SystemExit(0)
        if args.phase=='test':
            result=independent_test();print(json.dumps({'status':'test_completed','actions':len(result['actions']),'test_used':True}));raise SystemExit(0)
        baseline=json.loads((OUTPUT/'baseline.json').read_text(encoding='utf-8'))
        if baseline.get('frozen') or baseline.get('test_used'):raise RuntimeError('已冻结或已测试基线不自动重拟合')
        results=development_comparison(load_dataset(),baseline)
        for result in results['actions']:print(json.dumps({'action':result['action_id'],'primary':result['primary']},ensure_ascii=False))
