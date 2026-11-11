"""增量训练新动作分类头：只使用旧训练分区与新开发片，不读旧验证/测试集。"""
import sys,json,hashlib
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np
import joblib
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from threadpoolctl import threadpool_limits
from fitness_app.visual_recognition import normalize
from validate_gym_actions import CASES,OUT


def run():
    old=json.loads((ROOT/'data/experiments/visual-action-classifier.json').read_text(encoding='utf-8'))
    features=[];labels=[];sources=[]
    with np.load(ROOT/'data/experiments/visual-action-features.npz',allow_pickle=False) as cache:
        if str(cache['signature'])!=old['signature']:raise ValueError('旧训练缓存身份不同')
        for item in old['identity']['inputs']:
            if item['partition']!='train':continue
            key=hashlib.sha256(item['key'].encode()).hexdigest()[:24];f=cache[key]
            if not len(f):continue
            features.extend(f);labels.extend([item['action']]*len(f));sources.append({**item,'frames':len(f)})
    new_actions=[]
    training_cases=CASES[:6]+[case for case in CASES if case[1]=='commons-cdc-chest-press']
    for action,key,start,end,crop in training_cases:
        path=OUT/f'{key}-{start}-{end}.npz'
        with np.load(path,allow_pickle=False) as cache:
            meta=json.loads(str(cache['metadata']));all_features=cache['features'];f=all_features[np.isfinite(all_features).all(axis=1)]
        if not len(f):raise ValueError('新增输入缺少真实图像特征')
        features.extend(f);labels.extend([action]*len(f));new_actions.append(action)
        sources.append({'action':action,'key':key,'start_ms':start,'end_ms':end,'crop':crop,'frames':len(f),
                        'partition':'development_fit','source_sha256':meta['source_sha256'],
                        'cache_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'cache':str(path.relative_to(ROOT))})
    config={'C':.3,'class_weight':'balanced','max_iter':2000,'seed':20261022,'minimum_score':.70,'minimum_margin':.20}
    signature=hashlib.sha256(json.dumps({'sources':sources,'config':config},sort_keys=True).encode()).hexdigest()
    model_path=ROOT/'data/models/gym-action-classifier.joblib';manifest_path=model_path.with_suffix('.json')
    if manifest_path.exists():
        existing=json.loads(manifest_path.read_text(encoding='utf-8'))
        if existing['signature']==signature and hashlib.sha256(model_path.read_bytes()).hexdigest()==existing['sha256']:
            print(json.dumps({'reused':True,'frames':len(labels)}));return
    model=make_pipeline(StandardScaler(),LogisticRegression(C=config['C'],class_weight='balanced',max_iter=2000,random_state=config['seed']))
    with threadpool_limits(limits=2):model.fit(normalize(np.asarray(features)),labels)
    joblib.dump(model,model_path)
    info={'schema_version':1,'signature':signature,'version':'gym-linear-1-'+signature[:12],
          'sha256':hashlib.sha256(model_path.read_bytes()).hexdigest(),'config':config,'actions':list(model.classes_),
          'new_actions':new_actions,'training_frames':len(labels),'sources':sources,'scope':'development_reference',
          'independent_validation_passed':False,'test_used':False,'legacy_validation_used':False,
          'limitations':'New labels have only one original source each and share performers; no independent accuracy claim. Development clips are weak action labels, not error ground truth. FitnessScape and Gantner clips are excluded from fitting.'}
    manifest_path.write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'frames':len(labels),'classes':len(model.classes_),'new_classes':len(new_actions),'independent_validation_passed':False}))


if __name__=='__main__':run()
