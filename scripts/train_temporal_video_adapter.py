"""同预算对照：重复中心片段与不同时间段；固定4轮，原完整33类人口不变。"""
import hashlib,importlib.metadata,json,random,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np,torch
from fitness_app.domain_video_adviser import DomainVideoAdviser,verify_resources
from fitness_app.video_encoder_adaptation import configure,parameter_sha,loss_terms,apply_prediction,SCOPE
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.motion_evidence import evaluate as motion_evaluate
from fitness_app.research_extract import atomic_json
from scripts.train_chest_expert import read,sha,OUT,OLD
from scripts.partial_action_training import source_weights,allowed_mask
from scripts.train_video_encoder_adapter import save_checkpoint,overlay
from scripts.train_semantic_fusion import metrics_for_predictions
from scripts.encoder_adaptation_data import fingerprint
from scripts.temporal_video_inputs import prepare,load,materialise,PATH

PARENT=OUT/'domain-encoder-adapter-candidate.pt'
MODES=('center-control','multiview')
CONFIG={'epochs':4,'seed':20261025,'backbone_lr':1e-5,'head_lr':1e-4,'weight_decay':.01,'microbatch':2,'effective_batch':8,
        'anchor_strength':.001,'distillation_weight':.2,'minimum_score':.8,'minimum_margin':.25,'low_score_rescue':.7,
        'mode':'eval for fixed dropout, gradients enabled for selected52 parameters','teacher':'parent centre distribution repeated for class-consistent temporal views; only correct allowed high-confidence teachers protected',
        'selection':'final fixed fourth epoch in both matched-budget modes; no validation stopping or threshold selection'}
REPORT=OUT/'temporal-video-adapter-training.json'


def parent_resources():
    trained,manifest=verify_resources();report=read(OUT/'domain-encoder-adapter-training.json')
    if sha(PARENT)!=report['candidate_sha256'] or sha(OUT/'domain-encoder-adapter-plan.json')!=report['identity']['plan_sha256']:raise ValueError('父编码器候选身份改变')
    return trained,manifest,report


def parent_model(trained,manifest,parent_report):
    adviser=DomainVideoAdviser(trained,manifest,research=True);adviser._load();selected=configure(adviser.net);checkpoint=torch.load(PARENT,map_location='cpu',weights_only=True)
    if checkpoint['identity']!=read(OUT/'domain-encoder-adapter-plan.json') or checkpoint['identity']['base_checkpoint_sha256']!=manifest['sha256']:raise ValueError('父编码器增量与基座不同')
    overlay(adviser.net,selected,checkpoint)
    frozen=[n for n,p in adviser.net.named_parameters() if not p.requires_grad]
    if parameter_sha(adviser.net,frozen)!=parent_report['fit']['frozen_after']:raise ValueError('父编码器冻结前层不同')
    return adviser


def predict(net,pixel):
    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):value=net(torch.from_numpy(pixel[None]).cuda().float()).float().softmax(-1)[0].cpu().numpy()
    if value.shape!=(34,) or not np.isfinite(value).all() or np.any(value<0) or not np.isclose(value.sum(),1,atol=1e-5):raise ValueError('时序模型分布无效')
    return value


def teachers(data,centres,identity,trained,manifest,parent_report,verify):
    path=OUT/'temporal-video-parent-teachers.json';policy={'parent_sha256':sha(PARENT),'data_sha256':sha(PATH),'labels':manifest['labels'],'preprocessor_sha256':sha(ROOT/'data/models/xclip-base-patch32/preprocessor_config.json')}
    signature=fingerprint(policy);cache={'schema_version':1,'policy':policy,'signature':signature,'rows':[]}
    if path.exists():
        cache=read(path)
        if cache['signature']!=signature or fingerprint(cache['policy'])!=signature:raise ValueError('父教师缓存策略不同')
    records={r['id']:r for r in cache['rows']};output=[];adviser=None;calls=hits=0
    if len(records)!=len(cache['rows']):raise ValueError('父教师缓存身份重复')
    try:
        for row in data['bases']:
            entry={k:row[k] for k in ('id','source_sha256','start_ms','end_ms','crop','actual_ms')};key=row['id']
            if key in records:
                record=records[key]
                if record['input']!=entry:raise ValueError('父教师实际取帧不同')
                value=np.asarray(record['probability'],np.float32);hits+=1
            else:
                if verify:raise ValueError('缺少父教师缓存，核查模式不自动编码')
                if adviser is None:adviser=parent_model(trained,manifest,parent_report)
                value=predict(adviser.net,centres[key]);calls+=1;record={'id':key,'input':entry,'probability':value.tolist(),'probability_sha256':hashlib.sha256(value.tobytes()).hexdigest()};records[key]=record
                cache['rows']=list(records.values());atomic_json(path,cache)
                if calls%20==0:print(json.dumps({'stage':'temporal_parent_teachers','ready':len(output)+1,'total':len(data['bases']),'new_calls':calls}),flush=True)
            if value.shape!=(34,) or not np.isfinite(value).all() or np.any(value<0) or not np.isclose(value.sum(),1,atol=1e-5) or hashlib.sha256(value.tobytes()).hexdigest()!=record['probability_sha256']:raise ValueError('父教师分布缓存损坏')
            output.append(value)
        return np.asarray(output,np.float32),{'cache_hits':hits,'new_calls':calls,'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'parent_sha256':sha(PARENT)}
    finally:
        if adviser:adviser.close()


def fit(mode,adviser,data,centres,view_pixels,teacher,identity):
    net=adviser.net;selected=configure(net);frozen=[n for n,p in net.named_parameters() if not p.requires_grad];frozen_sha=parameter_sha(net,frozen)
    initial={n:p.detach().clone() for n,p in selected.items()};component_before={p:parameter_sha(net,[n for n in selected if n.startswith(p)]) for p in ('encoder.vision_model.encoder.layers.11.','encoder.mit.','head.')}
    optimizer=torch.optim.AdamW([{'params':[p for n,p in selected.items() if not n.startswith('head.')],'lr':CONFIG['backbone_lr']},{'params':[p for n,p in selected.items() if n.startswith('head.')],'lr':CONFIG['head_lr']}],weight_decay=CONFIG['weight_decay'])
    candidate=OUT/('temporal-video-'+mode+'.pt');progress=OUT/('temporal-video-'+mode+'-progress.pt');history=[];start_epoch=0;prior_seconds=0.;steps=0
    if progress.exists():
        previous=torch.load(progress,map_location='cpu',weights_only=True)
        if previous['identity']!=identity or previous['mode']!=mode or previous['frozen_sha']!=frozen_sha:raise ValueError('未完成训练属于另一时序条件')
        overlay(net,selected,previous);optimizer.load_state_dict(previous['optimizer']);history=previous['history'];start_epoch=previous['completed_epochs'];prior_seconds=previous['elapsed_seconds'];steps=previous['steps']
    bases=data['bases'];views=data['views'];expanded=[{**bases[v['base_index']],'id':v['id']} for v in views];indices=[v['base_index'] for v in views]
    mask=torch.tensor(allowed_mask(bases,adviser.labels,True)[indices],dtype=torch.bool,device='cuda');weights=torch.tensor(source_weights(expanded),dtype=torch.float32,device='cuda');reference=torch.tensor(teacher[indices],dtype=torch.float32,device='cuda')
    net.eval();begin=time.perf_counter();torch.cuda.reset_peak_memory_stats();torch.manual_seed(CONFIG['seed'])
    for epoch in range(start_epoch,CONFIG['epochs']):
        order=list(range(len(views)));random.Random(CONFIG['seed']+epoch).shuffle(order);ce_total=kl_total=0.;epoch_steps=0
        for offset in range(0,len(order),CONFIG['effective_batch']):
            chunk=order[offset:offset+CONFIG['effective_batch']];optimizer.zero_grad(set_to_none=True)
            for micro in range(0,len(chunk),CONFIG['microbatch']):
                selected_indices=chunk[micro:micro+CONFIG['microbatch']];batch=np.asarray([centres[views[i]['base_id']] if mode=='center-control' else view_pixels[views[i]['id']] for i in selected_indices],np.float16)
                with torch.autocast('cuda',dtype=torch.bfloat16):logits=net(torch.from_numpy(batch).cuda().float()).float()
                ce,kl,_=loss_terms(logits,mask[selected_indices],weights[selected_indices],reference[selected_indices]);loss=(ce.sum()+CONFIG['distillation_weight']*kl.sum())/len(chunk)
                if not torch.isfinite(loss):raise ValueError('时序训练损失非有限')
                loss.backward();ce_total+=float(ce.detach().sum());kl_total+=float(kl.detach().sum())
            anchor=CONFIG['anchor_strength']*sum((p-initial[n]).square().sum() for n,p in selected.items())/2;anchor.backward();torch.nn.utils.clip_grad_norm_(list(selected.values()),1.,error_if_nonfinite=True)
            if any(p.grad is not None for n,p in net.named_parameters() if n in frozen):raise ValueError('冻结前层出现训练梯度')
            if not any(p.grad is not None and torch.count_nonzero(p.grad)>0 for n,p in selected.items() if n.startswith('encoder.vision_model.encoder.layers.11.')):raise ValueError('时序实验没有实际视觉梯度')
            optimizer.step();steps+=1;epoch_steps+=1
        item={'epoch':epoch+1,'classification_loss':ce_total/len(views),'distillation_loss':kl_total/len(views),'anchor':float(anchor.detach()),'optimizer_steps':epoch_steps};history.append(item);elapsed=prior_seconds+time.perf_counter()-begin
        save_checkpoint(progress,{'identity':identity,'mode':mode,'frozen_sha':frozen_sha,'state_dict':{n:p.detach().cpu().clone() for n,p in selected.items()},'optimizer':optimizer.state_dict(),'history':history,'completed_epochs':epoch+1,'elapsed_seconds':elapsed,'steps':steps})
        print(json.dumps({'stage':'temporal_epoch','mode':mode,**item,'elapsed_seconds':round(elapsed,1)}),flush=True)
    if parameter_sha(net,frozen)!=frozen_sha:raise ValueError('冻结前层参数改变')
    component_after={p:parameter_sha(net,[n for n in selected if n.startswith(p)]) for p in component_before}
    if any(component_after[p]==component_before[p] for p in component_before):raise ValueError('视觉/时序/头未全部实际更新')
    fit_info={'training_runs':1,'bases':len(bases),'views':len(views),'completed_epochs':4,'steps':steps,'history':history,'elapsed_seconds':prior_seconds+time.perf_counter()-begin,
              'frozen_sha_before':frozen_sha,'frozen_sha_after':parameter_sha(net,frozen),'component_before':component_before,'component_after':component_after,'trainable_tensors':len(selected),'trainable_parameters':sum(p.numel() for p in selected.values()),'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/2**20,1)}
    checkpoint={'identity':identity,'mode':mode,'state_dict':{n:p.detach().cpu().clone() for n,p in selected.items()},'fit':fit_info,'runtime_enabled':False};save_checkpoint(candidate,checkpoint)
    checkpoint=torch.load(candidate,map_location='cpu',weights_only=True);overlay(net,selected,checkpoint);progress.unlink()
    return checkpoint


def evaluate_mode(mode,checkpoint,adviser,data,evaluation,routed,parent,expected,observed,predictor,verify,identity):
    candidate=OUT/('temporal-video-'+mode+'.pt');path=OUT/('temporal-video-'+mode+'-outputs.json');digest=sha(candidate)
    cache={'schema_version':1,'candidate_sha256':digest,'plan_sha256':fingerprint(identity),'rows':[]}
    if path.exists():
        cache=read(path)
        if cache['candidate_sha256']!=digest or cache['plan_sha256']!=fingerprint(identity):raise ValueError('时序输出缓存参数/计划不同')
    cached={r['id']:r for r in cache['rows']};calls=hits=0;by_base={r['id']:r['candidate'] for r in parent['rows']};by_route={r['id']:r for r in routed};output=[];verified={}
    from scripts.encoder_adaptation_data import pixels
    for row in evaluation:
        key=row['id'];base=by_base[key];result=dict(base);details={}
        if key in by_route:
            entry=by_route[key];entry_identity={k:entry[k] for k in ('id','source_sha256','start_ms','end_ms','crop','actual_ms')}
            if key in cached:
                record=cached[key]
                if record['input']!=entry_identity:raise ValueError('时序评估实际输入不同')
                value=np.asarray(record['probability'],np.float32);hits+=1
            else:
                if verify:raise ValueError('核查缺少时序输出，不自动调用GPU')
                if adviser is None:raise ValueError('缺少已载入的时序权重')
                value=predict(adviser.net,pixels(entry,adviser.config,verified));calls+=1;record={'id':key,'input':entry_identity,'probability':value.tolist(),'probability_sha256':hashlib.sha256(value.tobytes()).hexdigest()};cached[key]=record;cache['rows']=list(cached.values());atomic_json(path,cache)
            if value.shape!=(34,) or not np.isfinite(value).all() or np.any(value<0) or not np.isclose(value.sum(),1,atol=1e-5) or hashlib.sha256(value.tobytes()).hexdigest()!=record['probability_sha256']:raise ValueError('时序评估分布损坏')
            labels=identity['labels'];order=np.argsort(value)[::-1];top=labels[order[0]];score=float(value[order[0]]);margin=score-float(value[order[1]])
            pose=project_path(observed[key]['pose_cache']) if key in observed else OLD/(key+'-full-world-features.npz')
            if sha(pose)!=expected[key]['pose_cache_sha256']:raise ValueError('时序评估运动依据改变')
            with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','sizes','people')}
            motion=motion_evaluate(arrays,entry['actual_ms'][0],entry['actual_ms'][-1],top,predictor.info['motion_evidence']['config'])
            result=apply_prediction(base,top,score,margin,motion,'experimental-temporal-'+mode+'-'+digest[:12]);details={'top':top,'score':score,'margin':margin,'motion':motion}
        output.append({k:row[k] for k in ('id','source_sha256','group','partition')}|{'expected':row['action_id'],'parent':base,'candidate':result,'details':details,'changed':result['action_id']!=base['action_id']})
    results={}
    for part in ('validation','diagnostic'):
        subset=[r for r in output if r['partition']==part]
        facts=[{'id':r['id'],'source_sha256':r['source_sha256'],'group':r['group'],'expected':r['expected'],'predicted':r['candidate']['action_id'],'top':r['candidate'].get('top_action_id') or 'unknown','score':r['candidate']['score'],'margin':r['candidate']['margin']} for r in subset]
        results[part]=metrics_for_predictions(facts)
    raw=[r for r in output if r['partition']=='validation' and r['expected']!='unknown' and r['details']]
    return {'candidate_sha256':digest,'fit':checkpoint['fit'],'results':results,'rows':output,'new_evaluation_calls':calls,'evaluation_cache_hits':hits,'routed_raw':{'positive_clips':len(raw),'correct':sum(r['details']['top']==r['expected'] for r in raw)},'output_cache_sha256':sha(path),'runtime_enabled':False}


def main(verify=False):
    torch.set_num_threads(4);trained,manifest,parent_report=parent_resources();data,evaluation,routed,parent,expected,observed=load();predictor=SemanticPredictor()
    if parent['identity']['primary_manifest_sha256']!=sha(ROOT/'data/models/semantic-action-classifier.json'):raise ValueError('原生产策略不同')
    identity={'schema_version':1,'parameters':CONFIG,'data_sha256':sha(PATH),'parent_candidate_sha256':sha(PARENT),'parent_plan_sha256':sha(OUT/'domain-encoder-adapter-plan.json'),'labels':manifest['labels'],
              'primary_weight_sha256':predictor.info['sha256'],'evaluation_ids':[r['id'] for r in evaluation],'routed_ids':[r['id'] for r in routed],
              'code':{p:sha(ROOT/p) for p in ('scripts/train_temporal_video_adapter.py','scripts/temporal_video_inputs.py','fitness_app/temporal_training.py','fitness_app/video_encoder_adaptation.py','fitness_app/video_sampling.py','scripts/encoder_adaptation_data.py')},
              'versions':{p:importlib.metadata.version(p) for p in ('torch','transformers','av','Pillow')}}
    plan_path=OUT/'temporal-video-training-plan.json'
    if plan_path.exists():
        if read(plan_path)!=identity:raise ValueError('时序训练计划已冻结，不能按结果改输入/参数')
    else:atomic_json(plan_path,identity)
    print(json.dumps({'stage':'frozen_temporal_plan','bases':len(data['bases']),'views':len(data['views']),'routed':len(routed),'all_evaluation':len(evaluation),'epochs':4,'modes':MODES}),flush=True)
    config=read(ROOT/'data/models/xclip-base-patch32/preprocessor_config.json');centres=view_pixels=None
    if not verify:centres,view_pixels=materialise(data,config)
    teacher,teacher_info=teachers(data,centres,identity,trained,manifest,parent_report,verify);results={};new_fits=0
    for mode in MODES:
        adviser=None;candidate=OUT/('temporal-video-'+mode+'.pt')
        try:
            if candidate.exists():
                checkpoint=torch.load(candidate,map_location='cpu',weights_only=True)
                if checkpoint['identity']!=identity or checkpoint['mode']!=mode:raise ValueError('时序候选身份不同')
                if verify and sha(candidate)!=read(REPORT)['modes'][mode]['candidate_sha256']:raise ValueError('时序候选文件摘要改变')
                output_cache=OUT/('temporal-video-'+mode+'-outputs.json')
                if not verify and (not output_cache.exists() or len(read(output_cache)['rows'])!=len(routed)):
                    adviser=parent_model(trained,manifest,parent_report);overlay(adviser.net,configure(adviser.net),checkpoint)
            else:
                if verify:raise ValueError('缺少时序候选，不自动训练')
                adviser=parent_model(trained,manifest,parent_report);checkpoint=fit(mode,adviser,data,centres,view_pixels,teacher,identity);new_fits+=1
            results[mode]=evaluate_mode(mode,checkpoint,adviser,data,evaluation,routed,parent,expected,observed,predictor,verify,identity)
            print(json.dumps({'stage':'temporal_evaluated','mode':mode,'correct':round(results[mode]['results']['validation']['accuracy_including_rejections']*193),'routed_raw':results[mode]['routed_raw'],'new_evaluation_calls':results[mode]['new_evaluation_calls']}),flush=True)
        finally:
            if adviser:adviser.close()
    if results[MODES[0]]['fit']['steps']!=results[MODES[1]]['fit']['steps']:raise ValueError('两条件优化预算不匹配')
    after=results['multiview']['results']['validation'];before=parent['results']['validation']['candidate'];failures=[]
    for action,value in after['per_class'].items():
        prior=before['per_class'][action]
        if value['recall'] is None or value['recall']<.8:failures.append(action+': below80 or missing')
        if value['recall'] is not None and prior['recall'] is not None and value['recall']<prior['recall']:failures.append(action+': regression')
        if value['original_videos']<3 or value['groups']<2:failures.append(action+': source coverage insufficient')
    if after['negative_false_accepts']>before['negative_false_accepts']:failures.append('negative false accepts increased')
    if results['multiview']['results']['diagnostic']['accuracy_including_rejections']<parent['results']['diagnostic']['candidate']['accuracy_including_rejections']:failures.append('diagnostic regression')
    failures.append('independent error truth/real AI/mobile/full R3 acceptance pending')
    report={'schema_version':1,'scope':'matched optimizer budget and sources; parent trained video encoder, cleaned cable-Y and one new face-pull source, different video times versus centre repetition',
            'identity':identity,'teacher_cache':teacher_info,'new_training_runs':new_fits,'modes':results,'gate_passed':not failures,'gate_failures':failures,'runtime_enabled':False,
            'new_pose_inferences':0,'new_qwen_inferences':0,'frozen_new_validation_consumed':False,'reserved_publisher_consumed':False}
    if REPORT.exists():
        prior=read(REPORT);report['initial_run']=prior.get('initial_run') or {'new_training_runs':prior['new_training_runs'],'teacher_calls':prior['teacher_cache']['new_calls'],'new_evaluation_calls':{m:prior['modes'][m]['new_evaluation_calls'] for m in MODES}}
    atomic_json(REPORT,report)
    print(json.dumps({'completed':True,'correct':{m:round(results[m]['results']['validation']['accuracy_including_rejections']*193) for m in MODES},'steps_each':results[MODES[0]]['fit']['steps'],'gate_passed':report['gate_passed'],'runtime_enabled':False}),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--prepare']:parent_resources();prepare(read(ROOT/'data/models/xclip-base-patch32/preprocessor_config.json'))
    elif sys.argv[1:]==['--verify-candidate']:main(True)
    elif sys.argv[1:]:raise SystemExit('支持--prepare、无参数固定训练或--verify-candidate')
    else:main()
