"""固定人物划分实际训练EC3D错误分类基线，保留test人物一次评估。"""
import hashlib,json,sys,time
from pathlib import Path
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import joblib,numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from threadpoolctl import threadpool_limits
from fitness_app.ec3d_training import load,sequences,sequence_features,metrics,SPLIT
from fitness_app.research_extract import atomic_json
OUT=ROOT/'data/models/open-source-correction/ec3d'
REPORT=ROOT/'data/verification/round3/ec3d-training.json'

def candidate(name):
    if name=='relative_distance_linear':return make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=1000,class_weight='balanced',random_state=20261104))
    if name=='relative_distance_trees':return ExtraTreesClassifier(n_estimators=200,max_depth=12,min_samples_leaf=2,class_weight='balanced',n_jobs=2,random_state=20261104)
    raise ValueError('未知候选')


def run():
    labels,poses,source=load();items,excluded=sequences(labels,poses);features=np.asarray([sequence_features(p) for _,p in items]);identities=[r for r,_ in items]
    if any({r['subject'] for r in identities if r['partition']==a}&{r['subject'] for r in identities if r['partition']==b} for a,b in [('train','validation'),('train','test'),('validation','test')]):raise ValueError('人物泄漏')
    sets={key:{r['pose_sha256'] for r in identities if r['partition']==key} for key in SPLIT}
    if sets['test']&(sets['train']|sets['validation']):raise ValueError('测试与拟合含相同序列')
    reports=[];OUT.mkdir(parents=True,exist_ok=True)
    for action in ('SQUAT','Lunges','Plank'):
        began=time.monotonic();indices={key:np.array([i for i,r in enumerate(identities) if r['partition']==key and r['action']==action]) for key in SPLIT}
        ti,vi,ei=[indices[k] for k in ('train','validation','test')];y=np.array([r['label'] for r in identities]);comparisons=[]
        for name in ('relative_distance_linear','relative_distance_trees'):
            model=candidate(name)
            with threadpool_limits(limits=2):model.fit(features[ti],y[ti]);p=model.predict(features[vi])
            comparisons.append({'name':name,'validation':metrics(y[vi],p)})
        selected=max(comparisons,key=lambda r:(r['validation']['error_detection']['f2'],r['validation']['macro_recall']))
        # 方法在Sena验证后冻结，再用Hugues／Isinsu／Sena重拟合；Vidit不参与选法或阈值。
        model=candidate(selected['name']);fit=np.r_[ti,vi]
        with threadpool_limits(limits=2):model.fit(features[fit],y[fit]);prediction=model.predict(features[ei])
        result=metrics(y[ei],prediction);path=OUT/(action.lower()+'.joblib')
        joblib.dump({'model':model,'action':action,'feature_version':'ec3d-relative-pairwise-sequence-1',
            'fit_subjects':list(SPLIT['train']+SPLIT['validation']),'runtime_enabled':False,'license':'not_declared'},path,compress=3)
        restored=joblib.load(path)
        with threadpool_limits(limits=2):np.testing.assert_array_equal(prediction,restored['model'].predict(features[ei]))
        rows=[{**identities[i],'predicted_label':int(p),'error_detected':bool(p!=1)} for i,p in zip(ei,prediction)]
        report={'action':action,'train_sequences':len(ti),'validation_sequences':len(vi),'test_sequences':len(ei),
            'comparisons':comparisons,'selected':selected['name'],'test':result,'test_rows':rows,
            'model_path':path.relative_to(ROOT).as_posix(),'model_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'new_fits':3,'reload_prediction_equal':True,'elapsed_seconds':time.monotonic()-began,'runtime_enabled':False}
        reports.append(report);print(json.dumps({'action':action,'selected':selected['name'],'test':result},ensure_ascii=False),flush=True)
    result={'scope':'held_out_one_subject_original_3d_skeleton_not_rgb_or_live_camera_accuracy',
        'source_resource':source,'license':'not_declared_in_checked_repository','public_download_authorized_by_user':True,
        'split':{k:list(v) for k,v in SPLIT.items()},'split_source':'upstream dataset.py default subject indices [0,1]/[2]/[3] with alphabetical groupby subjects',
        'source_frames':len(labels),'source_sequences':len(items)+len(excluded),'included_sequences':len(items),'excluded_unknown':excluded,
        'partition_counts':dict(Counter(r['partition'] for r in identities)), 'source_sequence_index':identities,
        'feature_dimensions':features.shape[1],'actions':reports,'new_fits':9,'runtime_enabled':False,
        'project_frozen_test_used':False,'paid_api_calls':0,'private_media_uploaded':False,
        'limitations':['only_four_subjects_one_final_test_subject','no_current_mediapipe_rtmpose_3d_alignment',
            'source_label_knees_pass_toes_not_universal_error','unknown_class_excluded','no_framewise_error_onset_truth',
            'source_repository_license_not_declared','fps_unknown_no_absolute_speed_claim'],
        'implementation_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ('fitness_app/ec3d_training.py','scripts/train_ec3d.py')}}
    atomic_json(REPORT,result)
    atomic_json(OUT/'manifest.json',{'training_report':REPORT.relative_to(ROOT).as_posix(),'runtime_enabled':False,'license':'not_declared',
        'models':[{k:r[k] for k in ('action','model_path','model_sha256','selected')} for r in reports],
        'requires_before_runtime':['current_camera_coordinate_alignment','independent_real_video_and_view_validation','temporal_feedback_integration']})

if __name__=='__main__':run()
