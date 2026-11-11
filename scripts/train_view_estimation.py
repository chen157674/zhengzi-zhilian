"""按官方拍摄方位及人物划分训练辅助机位模型；复用已下载的真实姿态，不做GPU推理。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import argparse,collections,hashlib,json,re
import numpy as np,joblib
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.model_selection import GroupKFold
from threadpoolctl import threadpool_limits
from fitness_app.view_features import extract,VERSION,NAMES
from fitness_app.research_extract import atomic_json

BASE=ROOT/'data/verification/step-22';OUT=ROOT/'data/verification/round3';CLASSES=['front','oblique','side']


def metrics(labels,scores,threshold,margin):
    order=np.argsort(scores,axis=1);top=order[:,-1];pred=np.asarray(CLASSES)[top]
    accepted=(scores[np.arange(len(scores)),top]>=threshold)&(scores[np.arange(len(scores)),top]-scores[np.arange(len(scores)),order[:,-2]]>=margin)
    correct=pred==labels
    return {'frames':len(labels),'coverage':float(np.mean(accepted)),'accuracy_including_rejections':float(np.mean(correct&accepted)),
        'accepted_precision':float(np.mean(correct[accepted])) if accepted.any() else 0.,
        'opposite_view_errors':int(np.sum(accepted&(((pred=='front')&(labels=='side'))|((pred=='side')&(labels=='front'))))),
        'per_class':{c:{'frames':int(np.sum(labels==c)),'recall':float(np.mean((correct&accepted)[labels==c])),
            'accepted_precision':float(np.mean(correct[accepted&(pred==c)])) if np.any(accepted&(pred==c)) else 0.} for c in CLASSES}}


def make_model(name):
    if name.startswith('linear_'):return make_pipeline(StandardScaler(),LogisticRegression(C=float(name.split('_')[1]),max_iter=2000,class_weight='balanced',random_state=20261024))
    return ExtraTreesClassifier(n_estimators=200,min_samples_leaf=4,max_depth=14,max_features=1.,class_weight='balanced',n_jobs=4,random_state=20261024)


def fit(model,x,y,weights):
    model.fit(x,y,**({'logisticregression__sample_weight':weights} if hasattr(model,'named_steps') else {'sample_weight':weights}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--audit-only',action='store_true');args=parser.parse_args()
    manifest=json.loads((BASE/'semantic-video-features.json').read_text('utf8'));data=[];identities=[];partitions=collections.defaultdict(set)
    for entry in manifest['entries']:
        path=entry['path']
        if not (path.startswith('data/datasets/seven-exercises/') or path.startswith('data/datasets/development-movements/seven-')):continue
        match=re.search(r'subject_(\d+)_.*_(front|side|diagonal)\.mp4$',path)
        if not match or entry.get('crop'):continue
        group='kgbb3yn47p:subject_%03d'%int(match.group(1));label='oblique' if match.group(2)=='diagonal' else match.group(2)
        partitions[group].add(entry['partition']);pose=BASE/(entry['id']+'-full-world-features.npz')
        with np.load(pose,allow_pickle=False) as cache:
            identity=json.loads(str(cache['identity']))
            if any(identity[k]!=entry[k] for k in ('source_sha256','start_ms','end_ms','crop')):raise ValueError('视角缓存来源不匹配')
            for index,(points,size,people) in enumerate(zip(cache['points'],cache['sizes'],cache['people'])):
                feature=extract(points,float(size[0]),float(size[1]),int(people))
                if feature is not None:data.append((feature,label,group,entry['partition'],entry['source_sha256'],entry['id'],int(cache['timestamps'][index])))
        identities.append({'id':entry['id'],'source_sha256':entry['source_sha256'],'group':group,'partition':entry['partition'],'view':label,
            'cache_sha256':hashlib.sha256(pose.read_bytes()).hexdigest()})
    if any(len(v)>1 for v in partitions.values()):raise ValueError('按数据集人物规范化后训练/验证交叉')
    x=np.asarray([r[0] for r in data],np.float32);y=np.asarray([r[1] for r in data]);g=np.asarray([r[2] for r in data]);parts=np.asarray([r[3] for r in data])
    train=np.flatnonzero(parts=='train');valid=np.flatnonzero(parts=='validation');source_counts=collections.Counter((r[4],r[1]) for r in data)
    weights=np.asarray([1/source_counts[(r[4],r[1])] for r in data]);weights*=len(weights)/weights.sum()
    if args.audit_only:
        report=json.loads((OUT/'view-estimation-evaluation.json').read_text('utf8'));model=joblib.load(OUT/'view-model-candidate.joblib');scores=model.predict_proba(x[valid]);pred=np.asarray(CLASSES)[scores.argmax(1)]
        wrong=collections.Counter();examples={}
        for local,index in enumerate(valid):
            if pred[local]==y[index]:continue
            row=data[index];wrong[(row[5],row[1],str(pred[local]))]+=1
            score=float(scores[local].max());old=examples.get(row[5])
            if old is None or score>old['score']:examples[row[5]]={'id':row[5],'expected':row[1],'predicted':str(pred[local]),'score':score,'source_ms':row[6],'group':row[2]}
        report['validation_confusions']=[{'id':k[0],'expected':k[1],'predicted':k[2],'frames':v} for k,v in wrong.most_common()]
        report['high_confidence_error_examples']=sorted(examples.values(),key=lambda r:-r['score'])
        atomic_json(OUT/'view-estimation-evaluation.json',report)
        print('Top confusions',report['validation_confusions'][:8],flush=True)
        print('Examples',report['high_confidence_error_examples'][:6],flush=True);return
    candidates=[];chosen=None
    with threadpool_limits(limits=4):
      for name in ['linear_0.1','linear_1','linear_10','trees']:
        oof=np.zeros((len(train),3),np.float64)
        for a,b in GroupKFold(3).split(x[train],y[train],g[train]):
            model=make_model(name);fit(model,x[train[a]],y[train[a]],weights[train[a]])
            if list(model.classes_)!=CLASSES:raise ValueError('机位类别顺序改变')
            oof[b]=model.predict_proba(x[train[b]])
        for threshold,margin in [(0.,0.),(.6,.1),(.7,.15),(.8,.2),(.9,.3)]:
            m=metrics(y[train],oof,threshold,margin);safe=m['accepted_precision']>=.98 and all(v['accepted_precision']>=.98 for v in m['per_class'].values())
            score=(safe,m['accuracy_including_rejections'],-m['opposite_view_errors'])
            row={'model':name,'threshold':threshold,'margin':margin,'group_cross_validation':m};candidates.append(row)
            if chosen is None or score>chosen[0]:chosen=(score,row)
      selected=chosen[1];model=make_model(selected['model']);fit(model,x[train],y[train],weights[train]);scores=model.predict_proba(x[valid])
    measured=metrics(y[valid],scores,selected['threshold'],selected['margin'])
    passed=measured['accepted_precision']>=.95 and measured['coverage']>=.8 and all(v['recall']>=.8 for v in measured['per_class'].values())
    path=OUT/'view-model-candidate.joblib';joblib.dump(model,path,compress=3)
    info={'schema_version':1,'feature_version':VERSION,'feature_names':NAMES,'feature_code_sha256':hashlib.sha256((ROOT/'fitness_app/view_features.py').read_bytes()).hexdigest(),
        'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'path':path.relative_to(ROOT).as_posix(),'classes':CLASSES,'minimum_score':selected['threshold'],'minimum_margin':selected['margin'],
        'model':selected['model'],'source':'Mendeley official camera labels; normalized participant groups; no image action/quality label used as view truth',
        'pose_model_sha256':json.loads((ROOT/'data/models/manifest.json').read_text('utf8'))['sha256'] if (ROOT/'data/models/manifest.json').exists() else None,
        'runtime_enabled':False,'development_gate_passed':passed}
    atomic_json(OUT/'view-model-candidate.json',info)
    report={'scope':'view_classification_development_validation_not_technique_accuracy','selected':selected,'candidates':candidates,'validation':measured,
        'train_frames':len(train),'validation_frames':len(valid),'train_subjects':len(set(g[train])),'validation_subjects':len(set(g[valid])),
        'video_inputs':len(identities),'source_identities':identities,'view_model':info,'new_pose_inferences':0,'new_gpu_inferences':0,'runtime_enabled':False,'development_gate_passed':passed}
    atomic_json(OUT/'view-estimation-evaluation.json',report)
    print(json.dumps({k:report[k] for k in ('selected','validation','train_subjects','validation_subjects','development_gate_passed')},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
