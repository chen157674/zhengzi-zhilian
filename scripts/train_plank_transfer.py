"""共同二维特征训练与相机缓存探针；旧test已知，明确标为开发回归。"""
import hashlib,json,sys,time
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np,joblib
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
from fitness_app.ec3d_training import load,metrics
from fitness_app.plank_transfer import source_views,FEATURE_VERSION,classify,PlankGeometryEnsemble,per_subject_metrics,choose_operating_point
from fitness_app.research_extract import atomic_json
DEVELOPMENT=('Hugues','Isinsu','Sena');COMPARISON='Vidit'
REPORT=ROOT/'data/verification/round3/plank-transfer-training.json'
OUT=ROOT/'data/models/open-source-correction/plank-transfer'


def make(name):
    if name=='balanced_ensemble':return PlankGeometryEnsemble()
    if name=='forest':return ExtraTreesClassifier(n_estimators=160,max_depth=6,min_samples_leaf=4,class_weight='balanced',n_jobs=1,random_state=42)
    if name=='linear':return make_pipeline(StandardScaler(),LogisticRegression(C=.1,max_iter=1000,class_weight='balanced',random_state=20261104))
    return HistGradientBoostingClassifier(max_iter=100,max_leaf_nodes=7,l2_regularization=10.,class_weight='balanced',random_state=20261104)


def run():
    began=time.monotonic();previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None
    labels,poses,source=load();groups=defaultdict(list)
    for i,row in enumerate(labels):
        if row[0]=='Plank':groups[tuple(map(str,row[:4]))].append(i)
    entries=[];features=[];owners=[]
    for key,indices in groups.items():
        action,subject,label,rep=key;stamps=labels[indices,4].astype(int);order=np.argsort(stamps);indices=np.asarray(indices)[order]
        views=source_views(poses[indices]);identity={'id':'/'.join(key),'subject':subject,'label':int(label),'frames':len(indices),
            'pose_sha256':hashlib.sha256(poses[indices].astype('<f8').tobytes()).hexdigest(),'views':len(views)}
        if not views:raise ValueError('某原片没有适用投影，不能悄悄排除')
        owner=len(entries);entries.append(identity)
        for view in views:features.append(view['feature']);owners.append(owner)
    x=np.asarray(features);owners=np.asarray(owners);y=np.array([entries[i]['label'] for i in owners]);subjects=np.array([entries[i]['subject'] for i in owners])
    comparisons=[]
    for name in ('linear','tree','forest','balanced_ensemble'):
        oof=[];single_oof=[];folds=[]
        for held in DEVELOPMENT:
            fit=np.flatnonzero((subjects!=held)&np.isin(subjects,DEVELOPMENT));validation=np.flatnonzero(subjects==held)
            model=make(name)
            with threadpool_limits(limits=2):model.fit(x[fit],y[fit]);prob=model.predict_proba(x[validation])
            for index,p in zip(validation,prob):single_oof.append({'owner':int(owners[index]),'probabilities':p.tolist(),'label':int(y[index]),'subject':held})
            for owner in np.unique(owners[validation]):
                selected=prob[owners[validation]==owner].mean(0)
                oof.append({'owner':int(owner),'probabilities':selected.tolist(),'label':entries[owner]['label'],'subject':held})
            folds.append({'held_subject':held,'fit_subjects':[s for s in DEVELOPMENT if s!=held]})
        actual=np.array([r['label'] for r in single_oof]);prob=np.array([r['probabilities'] for r in single_oof]);options=[]
        for threshold in (.5,.6,.625,.65,.7,.725,.75,.8,.825,.85,.875,.9,.925):
            pred=classify(prob,model.classes_,threshold);score=metrics(actual,pred);normal=score['error_detection'];fpr=normal['fp']/(normal['fp']+normal['tn'])
            options.append({'threshold':threshold,'metrics':score,'normal_false_positive_rate':fpr,
                'per_subject':per_subject_metrics(single_oof,pred)})
        selected=choose_operating_point(options)
        comparisons.append({'name':name,'out_of_fold_subjects':folds,'threshold_sweep':options,'selected':selected,'oof':oof,'single_view_oof':single_oof,
            'selection_scope':'correlated_single_views_grouped_by_person_native_camera_has_no_multi_view_average'})
    feasible=[r for r in comparisons if r['selected']['subject_constraints_passed']]
    chosen=max(feasible,key=lambda r:(r['selected']['metrics']['error_detection']['f2'],min(f['metrics']['error_detection']['recall'] for f in r['selected']['per_subject']))) if feasible else min(comparisons,key=lambda r:max(f['normal_false_positive_rate'] for f in r['selected']['per_subject']))
    fit=np.flatnonzero(np.isin(subjects,DEVELOPMENT));check=np.flatnonzero(subjects==COMPARISON);model=make(chosen['name'])
    with threadpool_limits(limits=2):model.fit(x[fit],y[fit]);prob=model.predict_proba(x[check])
    rows=[]
    for owner in np.unique(owners[check]):
        p=prob[owners[check]==owner].mean(0);pred=int(classify(p[None],model.classes_,chosen['selected']['threshold'])[0])
        rows.append({**entries[owner],'prediction':pred,'probabilities':p.tolist()})
    result=metrics([r['label'] for r in rows],[r['prediction'] for r in rows])
    OUT.mkdir(parents=True,exist_ok=True);path=OUT/'candidate.joblib'
    joblib.dump({'model':model,'classes':model.classes_.tolist(),'threshold':chosen['selected']['threshold'],'feature_version':FEATURE_VERSION,
        'runtime_enabled':False,'source':source,'fit_subjects':list(DEVELOPMENT)},path,compress=3)
    restored=joblib.load(path)
    with threadpool_limits(limits=2):np.testing.assert_array_equal(restored['model'].predict_proba(x[check]),prob)
    report={'scope':'projected_2d_subject_grouped_development_comparison_not_fresh_independent_test','source':source,
        'feature_version':FEATURE_VERSION,'feature_dimensions':x.shape[1],'source_sequences':len(entries),'projected_training_rows':len(fit),
        'development_subjects':list(DEVELOPMENT),'previously_seen_comparison_subject':COMPARISON,'comparison_subject_excluded_from_fit_and_selection':True,
        'comparisons':comparisons,'selected':{'name':chosen['name'],**chosen['selected']},'comparison':result,'comparison_rows':rows,
        'source_sequences_index':entries,'new_fit_calls':13,'estimator_fits':17,'runtime_enabled':False,'reload_probabilities_equal':True,
        'model_path':path.relative_to(ROOT).as_posix(),'model_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'license':'not_declared','project_frozen_test_used':False,'paid_api_calls':0,'private_media_uploaded':False,
        'limitations':['synthetic_orthographic_views_not_real_video','four_subjects_only','previous_test_results_already_seen',
            'source_error_labels_not_equivalent_to_exact_hip_offset','native_camera_transfer_not_yet_validated'],
        'elapsed_seconds':time.monotonic()-began,'implementation_sha256':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ('fitness_app/plank_transfer.py','scripts/train_plank_transfer.py')}}
    folds=[]
    for subject in DEVELOPMENT:
        cases=[r for r in chosen['single_view_oof'] if r['subject']==subject]
        predicted=classify([r['probabilities'] for r in cases],model.classes_,chosen['selected']['threshold'])
        folds.append({'held_subject':subject,'scope':'correlated_projected_views_not_independent_sequences',
            'metrics':metrics([r['label'] for r in cases],predicted)})
    cases=chosen['oof'];predicted=classify([r['probabilities'] for r in cases],model.classes_,chosen['selected']['threshold'])
    if previous:
        report['previous_development_result']={'model_sha256':previous['model_sha256'],'selected_name':previous['selected']['name'],
            'threshold':previous['selected']['threshold'],'metrics':previous['selected']['metrics'],
            'per_subject':previous['selected'].get('per_subject_single_view_oof'),
            'native_review_is_separate':True,'scope':'same_development_people_prior_results_known_not_independent_gain'}
    report['selection_policy']={'overall_normal_false_positive_max':.2,'each_subject_normal_false_positive_max':.25,
        'score':'F2_then_worst_subject_error_recall','tuning_is_not_unbiased_final_evaluation':True}
    report['selected']['sequence_average_oof_at_selected_threshold']=metrics([r['label'] for r in cases],predicted)
    report['selected']['per_subject_single_view_oof']=folds
    report['release_gate']={'passed':False,'runtime_enabled':False,'reasons':['real_camera_error_labels_not_independently_confirmed',
        'only_one_public_camera_source','synthetic_orthographic_projection_is_not_camera_calibration',
        '25_comparison_sequences_previously_seen','source_class_to_user_visible_error_mapping_unverified'],
        'conclusion':'研究适配能接收同源近侧姿态但不发布错误事件'}
    atomic_json(REPORT,report)
    atomic_json(OUT/'manifest.json',{'runtime_enabled':False,'license':'not_declared','training_report':REPORT.relative_to(ROOT).as_posix(),
        'model_path':path.relative_to(ROOT).as_posix(),'sha256':report['model_sha256'],'feature_version':FEATURE_VERSION,
        'release_gate':report['release_gate'],'native_review':'data/verification/round3/plank-transfer-native-review.json'})
    print(json.dumps({'selected':report['selected'],'comparison':result,'source_sequences':len(entries),'elapsed':report['elapsed_seconds']},ensure_ascii=False),flush=True)

if __name__=='__main__':run()
