"""在本机GPU训练连续视频TCN；来源隔离验证，候选通过前不覆盖正式模型。"""
import argparse,collections,hashlib,json,random,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from fitness_app.video_sequence import CONFIG,window
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/step-22'
DIRECTORY=ROOT/'data/models/video-sequence'


def legacy_clips():
    """只拼接旧 train/validation 的实际时间戳缓存，不重新推理、不读取 test。"""
    report=json.loads((ROOT/'data/experiments/visual-action-classifier.json').read_text(encoding='utf-8'))
    baseline=json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    from fitness_app.recognition_inputs import extra_inputs
    extras={r['key']:r for r in extra_inputs()}
    with np.load(ROOT/'data/experiments/visual-action-features.npz',allow_pickle=False) as visual:
        if str(visual['signature'])!=report['signature']:raise ValueError('旧图像缓存身份不同')
        for entry in report['identity']['inputs']:
            if entry['partition'] not in {'train','validation'}:continue
            if entry['key'] in extras:path=project_path(extras[entry['key']]['cache'])
            elif entry['key'] in baseline['extractions']:
                extraction=baseline['extractions'][entry['key']]
                feature=json.loads((ROOT/'data/experiments/features'/f"{extraction['feature_key']}.json").read_text(encoding='utf-8'))
                path=project_path(feature['pose_cache'])
            else:raise ValueError('旧输入缺少可追溯的姿态')
            name=hashlib.sha256(entry['key'].encode()).hexdigest()[:24]
            f,ft=visual[name],visual[name+'_timestamps']
            with np.load(path,allow_pickle=False) as archive:
                stamps=archive['timestamps_ms'];points=archive['points'];sizes=archive['sizes'];people=archive['person_counts']
                identity=archive['identity_accepted'] if 'identity_accepted' in archive.files else np.ones(len(stamps),bool)
                features=np.full((len(stamps),512),np.nan,np.float32)
                # 只把已缓存的实际图像特征放回其采样时间，不制造新图像。
                for vector,stamp in zip(f,ft):
                    i=int(np.argmin(abs(stamps-stamp)))
                    if abs(int(stamps[i])-int(stamp))<=40:features[i]=vector
                arrays=dict(timestamps=stamps,points=points,sizes=sizes,people=people,features=features,identity=identity)
                if len(ft):
                    first=int(np.searchsorted(stamps,ft[0],side='left'))
                    arrays={k:v[first:] for k,v in arrays.items()}
                # 旧图像缓存只抽取最初8帧；其后未计算图像不等于识别器实际拒判。
                arrays['evaluation_end_ms']=int(ft[-1])+1000 if len(ft) else 0
                yield {**entry,'id':entry['key'],'action_id':entry['action']},arrays


def clips(allow_incomplete=False):
    yield from legacy_clips()
    segments=json.loads((ROOT/'data/datasets/development-movements/gym-segments.json').read_text(encoding='utf-8'))['segments']
    for segment in segments:
        if segment['partition'] not in {'train','validation','diagnostic'}:continue
        path=OUT/(segment['id']+'-sequence.npz')
        if not path.exists():
            if allow_incomplete:continue
            raise FileNotFoundError('连续特征尚未完成: '+segment['id'])
        with np.load(path,allow_pickle=False) as archive:
            arrays={key:archive[key].copy() for key in archive.files if key!='metadata'}
            info={**segment}
            if segment['source_id'].startswith('dvids-'):
                info['group']='public_dvids_collection'
            yield info,arrays


def dataset(allow_incomplete=False):
    images=[];poses=[];rows=[];rejections=[];groups=collections.defaultdict(set);hashes=collections.defaultdict(set)
    for info,arrays in clips(allow_incomplete):
        partition=info['partition'];groups[partition].add(info['group'])
        digest=info.get('source_sha256',info.get('sha256'));hashes[partition].add(digest)
        times=arrays['timestamps']
        if not len(times):continue
        # 不截取正式测试；验证的无效窗口也进入报告的拒判分母。
        for end in range(int(times[0])+CONFIG['minimum_span_ms'],min(int(times[-1]),arrays.get('evaluation_end_ms',int(times[-1])))+1,800):
            value,reason=window(arrays,end)
            row={'id':info['id'],'action':info['action_id'],'partition':partition,'group':info['group'],'end_ms':end,'reason':reason}
            if value is None:
                rejections.append(row);continue
            index=len(images);images.append(value[0]);poses.append(value[1]);rows.append({**row,'index':index})
    if groups['train']&groups['validation'] or hashes['train']&hashes['validation']:raise ValueError('视频训练与验证来源交叉')
    return np.asarray(images,np.float32),np.asarray(poses,np.float32),rows,rejections


def metrics(rows,rejections,probabilities,labels,partition):
    selected=[r for r in rows if r['partition']==partition];rejected=[r for r in rejections if r['partition']==partition]
    predictions=[]
    for row in selected:
        scores=probabilities[row['index']];order=np.argsort(scores)[::-1];score=float(scores[order[0]]);margin=score-float(scores[order[1]])
        top=labels[int(order[0])]
        predictions.append({**row,'top':top,'predicted':top if score>=.7 and margin>=.2 and not top.startswith('unknown') else None,'score':score,'margin':margin})
    predictions.extend({**r,'top':None,'predicted':None} for r in rejected)
    positives=[p for p in predictions if not p['action'].startswith('unknown')]
    negatives=[p for p in predictions if p['action'].startswith('unknown')]
    n=len(positives);covered=sum(p['predicted'] is not None for p in positives);correct=sum(p['predicted']==p['action'] for p in positives)
    per_class={}
    for action in labels:
        if action.startswith('unknown'):continue
        source=[p for p in predictions if p['action']==action];accepted=[p for p in predictions if p['predicted']==action]
        tp=sum(p['predicted']==action for p in source)
        per_class[action]={'windows':len(source),'clips':len({p['id'] for p in source}),
                           'recall':tp/len(source) if source else None,'precision':tp/len(accepted) if accepted else None,
                           'top1':sum(p['top']==action for p in source)/len(source) if source else None}
    recalls=[v['recall'] for v in per_class.values() if v['recall'] is not None]
    clip_scores=[]
    for key in {p['id'] for p in positives}:
        source=[p for p in positives if p['id']==key]
        clip_scores.append(sum(p['predicted']==p['action'] for p in source)/len(source))
    return {'windows':n,'accuracy_including_rejections':correct/n if n else None,'coverage':covered/n if n else None,
            'macro_recall_available_classes':float(np.mean(recalls)) if recalls else None,
            'mean_clip_accuracy_including_rejections':float(np.mean(clip_scores)) if clip_scores else None,
            'negative_windows':len(negatives),'negative_false_accept_rate':sum(p['predicted'] is not None for p in negatives)/len(negatives) if negatives else None,
            'accepted_precision':correct/covered if covered else None,'per_class':per_class,'predictions':predictions}


def run(epochs=50):
    import torch
    from torch.utils.data import TensorDataset,DataLoader,WeightedRandomSampler
    from video_sequence_model import VideoSequenceModel
    if not torch.cuda.is_available():raise RuntimeError('本次要求本机GPU训练；CUDA不可用，不静默切CPU')
    seed=20261022;random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed);torch.set_num_threads(4)
    images,poses,rows,rejections=dataset();labels=sorted({r['action'] for r in rows if r['partition']=='train'});mapping={a:i for i,a in enumerate(labels)}
    train=[r for r in rows if r['partition']=='train'];valid=[r for r in rows if r['partition']=='validation']
    if any(r['action'] not in mapping for r in valid):raise ValueError('验证动作未训练')
    counts=collections.Counter((r['action'],r['id']) for r in train);clip_counts=collections.Counter(a for a,k in counts)
    weights=[1/(counts[(r['action'],r['id'])]*clip_counts[r['action']]) for r in train]
    ix=np.asarray([r['index'] for r in train]);targets=torch.tensor([mapping[r['action']] for r in train],dtype=torch.long)
    data=TensorDataset(torch.from_numpy(images[ix]),torch.from_numpy(poses[ix]),targets)
    generator=torch.Generator().manual_seed(seed)
    sampler=WeightedRandomSampler(weights,max(1024,len(train)),replacement=True,generator=generator)
    loader=DataLoader(data,batch_size=64,sampler=sampler,num_workers=0,pin_memory=True)
    from fitness_app.gym_recognition import resources as text_resources
    text_resource,reason=text_resources()
    if text_resource is None:raise ValueError('缺少冻结CLIP文本先验: '+str(reason))
    texts=np.stack([text_resource['features'][text_resource['labels'].index(a)] if a!='unknown' else
                    np.mean([f for a,f in zip(text_resource['labels'],text_resource['features']) if a.startswith('unknown_')],axis=0) for a in labels])
    texts/=np.maximum(np.linalg.norm(texts,axis=1,keepdims=True),1e-12)
    strengths=np.asarray([max(.15,min(1.,clip_counts[a]/10)) for a in labels],np.float32)
    model=VideoSequenceModel(len(labels),texts,strengths).cuda();optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.01)
    criterion=torch.nn.CrossEntropyLoss(label_smoothing=.05);best=None;best_loss=float('inf');history=[];began=time.perf_counter()
    valid_indices=np.asarray([r['index'] for r in valid]);valid_targets=torch.tensor([mapping[r['action']] for r in valid],device='cuda')
    def predict(indices):
        output=[]
        with torch.inference_mode():
            for offset in range(0,len(indices),128):
                subset=indices[offset:offset+128]
                output.append(model(torch.from_numpy(images[subset]).cuda(),torch.from_numpy(poses[subset]).cuda()))
        return torch.cat(output) if output else torch.empty((0,len(labels)),device='cuda')
    for epoch in range(epochs):
        model.train();total=0
        for image,pose,target in loader:
            optimizer.zero_grad(set_to_none=True)
            logits=model(image.cuda(non_blocking=True),pose.cuda(non_blocking=True));loss=criterion(logits,target.cuda(non_blocking=True))
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step();total+=float(loss.detach())
        model.eval();logits=predict(valid_indices);validation_loss=float(criterion(logits,valid_targets))
        if validation_loss<best_loss:
            best_loss=validation_loss;best={key:value.detach().cpu().clone() for key,value in model.state_dict().items()}
        history.append({'epoch':epoch+1,'train_loss':total/len(loader),'validation_loss':validation_loss,'validation_eligible_top1':float((logits.argmax(1)==valid_targets).float().mean())})
        if epoch==0 or (epoch+1)%5==0:print(json.dumps(history[-1]),flush=True)
    model.load_state_dict(best);model.eval();probabilities=predict(np.arange(len(rows))).softmax(-1).cpu().numpy()
    result=metrics(rows,rejections,probabilities,labels,'validation');diagnostic=metrics(rows,rejections,probabilities,labels,'diagnostic')
    # 同一训练/验证输入比较图像平均基线，不能用TCN训练集成绩声称视频方法更好。
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from threadpoolctl import threadpool_limits
    image_mask=images[:,:,512:513]
    image_means=(images[:,:,:512]*image_mask).sum(1)/np.maximum(image_mask.sum(1),1)
    baseline=make_pipeline(StandardScaler(),LogisticRegression(C=.3,max_iter=2000,random_state=seed))
    with threadpool_limits(limits=2):
        baseline.fit(image_means[ix],[r['action'] for r in train],logisticregression__sample_weight=np.asarray(weights)*len(weights)/sum(weights))
        image_probabilities=baseline.predict_proba(image_means)
    if list(baseline.classes_)!=labels:raise ValueError('图像比较基线标签不同')
    image_comparison=metrics(rows,rejections,image_probabilities,labels,'validation')
    eligible=[v['recall'] for v in result['per_class'].values()]
    from fitness_app.automatic_recognition import TARGET_ACTIONS
    from fitness_app.gym_catalogue import ACTION_IDS
    expected=list(TARGET_ACTIONS)+list(ACTION_IDS)
    missing_train=[a for a in expected if a not in labels]
    passed=bool(not missing_train and result['accuracy_including_rejections']>=.85 and result['coverage']>=.8 and all(v is not None and v>=.7 for v in eligible))
    source_identity=[{k:r[k] for k in ('id','action','partition','group','end_ms','reason')} for r in rows+rejections]
    signature=hashlib.sha256(json.dumps({'samples':source_identity,'config':CONFIG,
                                       'image_features_sha256':hashlib.sha256(images.tobytes()).hexdigest(),
                                       'pose_features_sha256':hashlib.sha256(poses.tobytes()).hexdigest(),
                                       'feature_code_sha256':hashlib.sha256((ROOT/'fitness_app/video_sequence.py').read_bytes()).hexdigest(),
                                       'model_code':hashlib.sha256(Path(__file__).with_name('video_sequence_model.py').read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
    DIRECTORY.resolve().relative_to(ROOT);DIRECTORY.mkdir(exist_ok=True)
    checkpoint=DIRECTORY/'candidate.pt';torch.save({'state_dict':best,'labels':labels,'config':CONFIG,'signature':signature},checkpoint)
    report={'schema_version':1,'signature':signature,'feature_config':CONFIG,'training':'video-level contiguous 32-frame ordered image+pose TCN; frozen image encoder; train-only gradients',
            'selection':'lowest development validation loss; this is NOT a blind independent test','epochs':epochs,'history':history,
            'semantic_prior':{'model_version':text_resource['version'],'logit_scale':80,'maximum_residual':4,'per_class_strength':dict(zip(labels,map(float,strengths)))},
            'train_windows':len(train),'validation':result,'image_only_comparison':image_comparison,
            'diagnostic_same_publisher':diagnostic,'classes':labels,'missing_training_actions':missing_train,
            'missing_validation_actions':[a for a in expected if a not in result['per_class'] or result['per_class'][a]['windows']==0],
            'legacy_cache_scope':'Original cached first 8 image samples only; no uncached tail or original test used',
            'train_clips_per_class':dict(clip_counts),
            'input_clips':len({r['id'] for r in rows+rejections}),'rejection_reasons':dict(collections.Counter(r['reason'] for r in rejections)),
            'hardware':{'gpu':torch.cuda.get_device_name(0),'vram_gib':round(torch.cuda.get_device_properties(0).total_memory/2**30,2),'torch':torch.__version__,'cuda':torch.version.cuda,
                        'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/2**20,1)},'elapsed_seconds':round(time.perf_counter()-began,1),
            'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'test_used':False,'runtime_enabled':False,
            'development_gate_passed':passed,'specific_error_validation_passed':False}
    report_path=OUT/'video-sequence-training.json'
    if report_path.exists():
        prior=json.loads(report_path.read_text(encoding='utf-8'))
        report['previous_candidate_comparisons']=prior.get('previous_candidate_comparisons',[])+[{
            'signature':prior['signature'],'validation':{k:v for k,v in prior['validation'].items() if k not in ('predictions','per_class')},
            'epochs':prior['epochs'],'note':'Prior video candidate without bounded text semantic residual; development comparison, not blind test'}]
    atomic_json(report_path,report)
    atomic_json(DIRECTORY/'manifest.json',{k:report[k] for k in ('signature','feature_config','classes','checkpoint_sha256','test_used','runtime_enabled','development_gate_passed','hardware')})
    print(json.dumps({'classes':len(labels),'train_windows':len(train),'validation_accuracy':result['accuracy_including_rejections'],'coverage':result['coverage'],'passed':passed,'elapsed_seconds':report['elapsed_seconds'],'hardware':report['hardware']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--epochs',type=int,default=50);run(parser.parse_args().epochs)
