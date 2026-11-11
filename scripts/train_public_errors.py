"""真实训练两类公开纠错候选；源test只在模型选择后评估，不修改项目保留集。"""
import csv,hashlib,json,sys,time
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import joblib,numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from fitness_app.error_training import features,fingerprints,blocked_split,metrics,trunk_features
from fitness_app.research_extract import atomic_json
DATA=ROOT/'data/datasets/open-source-correction/exercise-correction'
OUT=ROOT/'data/models/open-source-correction/trained-errors'
REPORT=ROOT/'data/verification/round3/public-error-training.json'

def read(manifest,task,split):
    row=next(r for r in manifest['files'] if r['source_path']==f'core/{task}_model/{split}.csv')
    path=project_path(row['path']);raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('数据摘要改变')
    with path.open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream);columns=[c for c in reader.fieldnames if c!='label'];rows=list(reader)
    x=np.array([[float(r[c]) for c in columns] for r in rows],np.float64);y=np.array([r['label'] for r in rows])
    if not np.isfinite(x).all() or not set(y)<={'C','H','L'}:raise ValueError('训练坐标或标签非法')
    return x,y,columns,row


def train(task,manifest):
    began=time.monotonic();x,y,columns,identity=read(manifest,task,'train');original=len(y)
    hashes=fingerprints(x);seen={};keep=[];conflicts=set()
    for i,(digest,label) in enumerate(zip(hashes,y)):
        if digest in seen:
            if y[seen[digest]]!=label:conflicts.add(digest)
        else:seen[digest]=i;keep.append(i)
    keep=[i for i in keep if hashes[i] not in conflicts];x=x[keep];y=y[keep];hashes=hashes[keep]
    ti,vi=blocked_split(y);comparisons=[];fitted=[]
    candidates=[('raw_linear',False,make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=800,class_weight='balanced',random_state=20261104))),
            ('normalized_tree',True,HistGradientBoostingClassifier(max_iter=140,max_leaf_nodes=15,l2_regularization=3.,learning_rate=.08,class_weight='balanced',random_state=20261104))]
    if task=='bicep':candidates.append(('relative_trunk_linear','trunk',make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=800,class_weight='balanced',random_state=20261104))))
    for name,normalized,model in candidates:
        values=trunk_features(x,columns) if normalized=='trunk' else features(x,columns,normalized)
        with threadpool_limits(limits=2):model.fit(values[ti],y[ti]);prediction=model.predict(values[vi])
        result=metrics(y[vi],prediction);comparisons.append({'name':name,'normalized':normalized,'validation':result});fitted.append(model)
    chosen=max(range(len(comparisons)),key=lambda i:(comparisons[i]['validation']['error_detection']['f2'],comparisons[i]['validation']['macro_recall']))
    spec=comparisons[chosen];model=fitted[chosen]
    # 选择在内部分块验证冻结，源test未用于拟合／选模型或阈值。
    tx,ty,tcolumns,test_identity=read(manifest,task,'test')
    if columns!=tcolumns:raise ValueError('源训练／测试列不一致')
    test_hashes=fingerprints(tx);overlap=np.isin(test_hashes,hashes);test_conflicts=int(sum(ty[i]!=y[seen_index] for i,h in enumerate(test_hashes) if h in set(hashes) for seen_index in [int(np.flatnonzero(hashes==h)[0])]))
    unique=~overlap
    test_x=trunk_features(tx[unique],columns) if spec['normalized']=='trunk' else features(tx[unique],columns,spec['normalized'])
    with threadpool_limits(limits=2):prediction=model.predict(test_x)
    evaluation=metrics(ty[unique],prediction)
    perturbations={}
    for name,scale,dx,dy in [('translate',1.,.08,-.04),('scale_small',.8,0.,0.),('scale_large',1.2,0.,0.)]:
        changed=tx[unique].copy()
        for j,c in enumerate(columns):
            if c.endswith('_x'):changed[:,j]=(changed[:,j]-.5)*scale+.5+dx
            elif c.endswith('_y'):changed[:,j]=(changed[:,j]-.5)*scale+.5+dy
            elif c.endswith('_z'):changed[:,j]*=scale
        with threadpool_limits(limits=2):p=model.predict(trunk_features(changed,columns) if spec['normalized']=='trunk' else features(changed,columns,spec['normalized']))
        perturbations[name]={'prediction_agreement':float(np.mean(p==prediction)), 'metrics':metrics(ty[unique],p),
            'scope':'coordinate_only_transform_not_new_camera_or_new_subject'}
    geometry_probe=None
    if task=='bicep':
        gi=next(i for i,c in enumerate(comparisons) if c['name']=='relative_trunk_linear')
        gp=OUT/(task+'-geometry.joblib');OUT.mkdir(parents=True,exist_ok=True)
        joblib.dump({'model':fitted[gi],'columns':columns,'normalized':'trunk','task':task,'runtime_enabled':False,'source_commit':manifest['commit']},gp,compress=3)
        with threadpool_limits(limits=2):gpred=fitted[gi].predict(trunk_features(tx[unique],columns))
        geometry_probe={'name':'relative_trunk_linear','internal_validation':comparisons[gi]['validation'],
            'source_test_metrics':metrics(ty[unique],gpred),'model_path':gp.relative_to(ROOT).as_posix(),
            'model_sha256':hashlib.sha256(gp.read_bytes()).hexdigest(),'runtime_enabled':False,
            'status':'followup_after_external_domain_failure_not_fresh_independent_experiment'}
    OUT.mkdir(parents=True,exist_ok=True);path=OUT/(task+'.joblib')
    joblib.dump({'model':model,'columns':columns,'normalized':spec['normalized'],'task':task,'runtime_enabled':False,'source_commit':manifest['commit']},path,compress=3)
    restored=joblib.load(path)
    with threadpool_limits(limits=2):np.testing.assert_array_equal(restored['model'].predict(test_x),prediction)
    # 原test高分不能证明人物隔离，更不能据此打开生产判项。
    result={'task':task,'original_train_rows':original,'unique_train_rows':len(y),'removed_duplicate_or_conflict_rows':original-len(y),
        'conflicting_coordinate_hashes':len(conflicts),'train_classes':dict(Counter(y)),'fit_rows':len(ti),'validation_rows':len(vi),
        'purged_boundary_rows':len(y)-len(ti)-len(vi),'internal_split':'within_label_run_128row_blocks_every_fifth_holdout_16row_purge_not_subject_split',
        'comparisons':comparisons,'selected':spec['name'],'selection_metric':'internal_validation_binary_error_F2_then_macro_recall',
        'source_test_rows':len(ty),'exact_train_test_overlaps_removed':int(overlap.sum()),'overlap_label_conflicts':test_conflicts,
        'source_test_metrics_after_exact_dedup':evaluation,'coordinate_robustness':perturbations,'new_fits':len(candidates),
        'train_resource':identity,'test_resource':test_identity,'model_path':path.relative_to(ROOT).as_posix(),
        'model_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'reload_prediction_equal':True,
        'geometry_followup':geometry_probe,'runtime_enabled':False,'independent_subject_accuracy_validated':False,'elapsed_seconds':time.monotonic()-began}
    print(json.dumps({'task':task,'selected':spec['name'],'fit_rows':len(ti),'source_test':evaluation},ensure_ascii=False),flush=True)
    return result

if __name__=='__main__':
    manifest=json.loads((DATA/'manifest.json').read_text(encoding='utf-8'))
    report={'source':{'repo':manifest['repo'],'commit':manifest['commit'],'license':manifest['license']},
        'scope':'new_training_public_error_coordinates_not_independent_subject_or_live_video_accuracy',
        'project_frozen_test_used':False,'private_media_uploaded':False,'paid_api_calls':0,
        'runtime_enabled':False,'tasks':[train(task,manifest) for task in ('plank','bicep')],
        'limitations':['original_images_removed_by_upstream','subject_and_video_ids_not_provided','adjacent_frame_leakage_cannot_be_fully_excluded',
            'no_hand_grip_or_wrist_error_labels','2D_source_aspect_ratios_unknown','requires_native_video_cross_subject_validation_before_runtime']}
    atomic_json(REPORT,report)
