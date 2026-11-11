"""按冻结数据划分推进研究；默认只做开发集先导，不读取独立测试图像。"""
import argparse
import json
import sys
import os
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GLOG_minloglevel','2')
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL','2')
from fitness_app.research_data import load_dataset,split_snapshot,SUPPORTED
from fitness_app.research_extract import OUTPUT,pose_config,atomic_json,extract
from fitness_app.research_features import CONFIG,CONFIG_SHA256
from fitness_app.database import maintenance_lock
from fitness_app.paths import runtime_path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--phase',choices=('pilot','development'),default='pilot');parser.add_argument('--retry-failed',action='store_true');args=parser.parse_args()
    samples=load_dataset();split=split_snapshot(samples);config=pose_config()
    split_path=OUTPUT/'split.json'
    if split_path.exists():
        previous=json.loads(split_path.read_text(encoding='utf-8'))
        if previous!=split:raise RuntimeError('已有划分快照不一致，禁止覆盖')
    else:atomic_json(split_path,split)
    eligible=[sample for sample in samples if sample.partition in {'train','validation','development'}]
    if args.phase=='pilot':
        eligible=[next(sample for sample in eligible if sample.action_id==action and sample.label==label) for action in sorted(SUPPORTED) for label in (('GB','PB') if action=='conventional_deadlift' else ('good','bad'))]
    manifest_path=OUTPUT/'baseline.json'
    baseline=json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'schema_version':1,'test_used':False,'frozen':False,'extractions':{}}
    if baseline.get('frozen') or baseline.get('test_used'):raise RuntimeError('基线已冻结或用过测试，不自动继续开发修改')
    baseline.update(split_sha256=split['split_sha256'],pose_config=config,feature_config=CONFIG,feature_config_sha256=CONFIG_SHA256,phase=args.phase)
    atomic_json(manifest_path,baseline)
    for index,sample in enumerate(eligible,1):
        result=extract(sample,config,retry_failed=args.retry_failed);baseline['extractions'][sample.key]={key:result.get(key) for key in ('feature_key','pose_key','status','rejection_reason','error_code','coverage','decoded_frames','elapsed_ms')}
        atomic_json(manifest_path,baseline)
        print(json.dumps({'index':index,'total':len(eligible),'action':sample.action_id,'subject_group':sample.subject_group,'view':sample.views,
            'status':result['status'],'coverage':result.get('coverage'),'rejection':result.get('rejection_reason'),'cache_hit':result['cache_hit'],'elapsed_ms':result['elapsed_ms']},ensure_ascii=False),flush=True)


if __name__=='__main__':
    # 研究 CLI 与正式服务互斥，避免绕开本机单视觉任务约定。
    with maintenance_lock(runtime_path()/'service.lock',exact=True):main()
