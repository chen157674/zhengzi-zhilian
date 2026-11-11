"""只运行新增时序候选；默认复用匹配结果，不调用姿态模型。"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import configure_environment
configure_environment()
from fitness_app.deadlift_temporal import development

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--reuse-only',action='store_true')
    result,hit=development(parser.parse_args().reuse_only)
    print(json.dumps({'cache_hit':hit,'new_training_fits':0 if hit else result['new_training_fits'],
        'new_pose_inference_frames':0,'metrics':result['metrics']['main'],
        'acceptance_passed':result['metrics']['acceptance_passed'],'test_used':False},ensure_ascii=False))
