"""公开弯举候选在既有开发视频原生MediaPipe缓存上的迁移检查，不读项目test。"""
import hashlib,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import joblib,numpy as np
from threadpoolctl import threadpool_limits
from fitness_app.error_training import features,trunk_features
from fitness_app.research_data import load_dataset
from fitness_app.research_extract import atomic_json
from scripts.diagnose_cycles import selected_samples,cached_frames
NAMES={'nose':0,'left_shoulder':11,'right_shoulder':12,'left_elbow':13,'right_elbow':14,
    'left_wrist':15,'right_wrist':16,'left_hip':23,'right_hip':24}

def main():
    report_path=ROOT/'data/verification/round3/public-error-training.json';report=json.loads(report_path.read_text(encoding='utf-8'))
    trained=next(t for t in report['tasks'] if t['task']=='bicep');path=ROOT/trained['model_path']
    if hashlib.sha256(path.read_bytes()).hexdigest()!=trained['model_sha256']:raise ValueError('候选模型身份改变')
    bundle=joblib.load(path);geometry=joblib.load(ROOT/trained['geometry_followup']['model_path']) if trained.get('geometry_followup') else None;baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'));rows=[]
    samples=selected_samples(load_dataset(),'standing_dumbbell_curl')
    for sample in samples:
        if sample.partition!='train':raise ValueError('仅本轮未拟合的已有训练分区开发探针，不访问项目test')
        frames=cached_frames(sample,baseline,apply_bounds=False);values=[];times=[]
        for frame in frames:
            points=frame['framing']['landmarks']
            if len(points)!=33 or not frame['identity_accepted']:continue
            if not all(points[i].get('valid') for i in (11,12,23,24)):continue
            if not any(all(points[i].get('valid') for i in ids) for ids in ((11,13,15),(12,14,16))):continue
            value=[]
            for column in bundle['columns']:
                name,key=column.rsplit('_',1);point=points[NAMES[name]];key='visibility' if key=='v' else key
                value.append(point.get(key,float('nan')))
            if not np.isfinite(value).all():continue
            values.append(value);times.append(frame['captured_ms'])
        if values:
            with threadpool_limits(limits=2):prob=bundle['model'].predict_proba(features(values,bundle['columns'],bundle['normalized']))
            index=list(bundle['model'].classes_).index('L');p=prob[:,index];prediction=p>=.5
            result={'candidate_frames':int(prediction.sum()),'accepted_coordinate_frames':len(values),'error_probability_median':float(np.median(p)),
                'candidate_fraction':float(prediction.mean()),'clip_majority_candidate':bool(prediction.mean()>=.5)}
        else:result={'candidate_frames':0,'accepted_coordinate_frames':0,'clip_majority_candidate':None}
        if values and geometry is not None:
            with threadpool_limits(limits=2):gprob=geometry['model'].predict_proba(trunk_features(values,geometry['columns']))
            gp=gprob[:,list(geometry['model'].classes_).index('L')]
            result['geometry_followup']={'candidate_fraction':float(np.mean(gp>=.5)),
                'clip_majority_candidate':bool(np.mean(gp>=.5)>=.5),'error_probability_median':float(np.median(gp))}
        rows.append({'path':sample.path,'source_sha256':sample.sha256,'subject':sample.subject_group,'partition':sample.partition,
            'source_coarse_label':sample.label,'label_is_specific_lean_back_truth':False,'frames':len(frames),**result})
    report['external_development_probe']={'scope':'12_other_dataset_training_partition_clips_no_specific_leanback_truth',
        'new_pose_inferences':0,'project_test_used':False,'rows':rows,
        'normal_coarse_clips_majority_flagged':sum(r['source_coarse_label']=='good' and r['clip_majority_candidate'] is True for r in rows),
        'normal_coarse_clips':sum(r['source_coarse_label']=='good' for r in rows),
        'geometry_normal_coarse_clips_majority_flagged':sum(r['source_coarse_label']=='good' and r.get('geometry_followup',{}).get('clip_majority_candidate') is True for r in rows),
        'runtime_enabled':False,'reason':'no_subject_disjoint_specific_error_validation_or_temporal_integration'}
    atomic_json(report_path,report)
    print(json.dumps(report['external_development_probe'],ensure_ascii=False),flush=True)

if __name__=='__main__':main()
