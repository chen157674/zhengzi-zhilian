"""旧8段保留视频仅补运动确认检查，复用原分类结果；不再作为独立保留集。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.semantic_model import resources
from fitness_app.motion_evidence import evaluate,apply
from fitness_app.gym_pose import GymPoseService
from extract_semantic_video import seek_safe_frames

OUT=ROOT/'data/verification/round3';CACHE=OUT/'motion-heldout-pose.npz';REPORT=OUT/'motion-heldout-regression.json'


def main():
    original=json.loads((ROOT/'data/verification/step-22/semantic-holdout.json').read_text('utf8'));info,reason=resources()
    if not info or not info.get('motion_evidence'):raise RuntimeError('已发布运动保护不可用：'+str(reason))
    if original['policy']['head_sha256']!=info['sha256'] or original['policy']['version']!=info['base_version'] or original['policy']['encoder_policy_sha256']!=info['encoder_policy_sha256']:
        raise ValueError('原分类结果与基础模型不一致，不能冒充复用')
    identity=[{'id':r['entry']['id'],'sha256':r['entry']['source_sha256'],'crop':r['entry']['crop'],'times':r['sampled_timestamps_ms']} for r in original['rows']]
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();new_pose_calls=0
    if CACHE.exists():
        with np.load(CACHE,allow_pickle=False) as cache:
            if str(cache['signature'])!=signature:raise ValueError('旧运动检查缓存身份不一致')
            arrays={k:cache[k].copy() for k in ('timestamps','points','sizes','people')}
    else:
        all_arrays={k:[] for k in ('timestamps','points','sizes','people')}
        for row in original['rows']:
            entry=row['entry'];path=project_path(entry['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=entry['source_sha256']:raise ValueError('原视频身份改变')
            pictures,times=seek_safe_frames(entry)
            if list(map(int,times))!=row['sampled_timestamps_ms']:raise ValueError('实际采样时间改变，未套用旧分类')
            values={k:[] for k in all_arrays}
            with GymPoseService(video=True) as pose:
                for stamp,rgb in zip(times,pictures):
                    result=pose.detect(rgb,int(stamp));new_pose_calls+=1;p=np.full((33,5),np.nan,np.float32)
                    if len(result['poses'])==1:p=np.asarray([[v[k] for k in ('x','y','z','visibility','presence')] for v in result['poses'][0]],np.float32)
                    for k,v in {'timestamps':int(stamp),'points':p,'sizes':(rgb.shape[1],rgb.shape[0]),'people':len(result['poses'])}.items():values[k].append(v)
            for k in all_arrays:all_arrays[k].append(np.asarray(values[k]))
        arrays={k:np.asarray(v) for k,v in all_arrays.items()};np.savez_compressed(CACHE,signature=np.asarray(signature),**arrays)
    rows=[]
    for index,row in enumerate(original['rows']):
        subset={k:v[index] for k,v in arrays.items()};times=subset['timestamps'];before=row['prediction']
        evidence=evaluate(subset,int(times[0]),int(times[-1]),before['action_id'],info['motion_evidence']['config']);after=apply(before,evidence)
        rows.append({'id':row['entry']['id'],'source_sha256':row['entry']['source_sha256'],'expected':row['entry']['action_id'],
            'action_before':before['action_id'],'action_after':after['action_id'],'motion_evidence':evidence})
    report={'scope':'already_observed_8_source_regression_cached_classification_not_independent_accuracy','base_version':info['base_version'],'model_version':info['version'],
        'correct_before':sum(r['action_before']==r['expected'] for r in rows),'correct_after':sum(r['action_after']==r['expected'] for r in rows),
        'clips':len(rows),'rows':rows,'new_pose_calls':new_pose_calls,'new_gpu_inferences':0,'weights_changed':False,'cache_sha256':hashlib.sha256(CACHE.read_bytes()).hexdigest()}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print({k:report[k] for k in ('clips','correct_before','correct_after','new_pose_calls','new_gpu_inferences')},flush=True)
    print('motion states',[(r['id'],r['motion_evidence']['state']) for r in rows],flush=True)


if __name__=='__main__':main()
