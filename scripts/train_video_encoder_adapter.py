"""固定4轮更新视频视觉末层和时序模块；原完整33类评估，无自动发布。"""
import hashlib,json,math,random,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np,torch
from fitness_app.video_encoder_adaptation import configure,parameter_sha,loss_terms,apply_prediction,SCOPE
from fitness_app.domain_video_adviser import verify_resources,DomainVideoAdviser
from fitness_app.semantic_model import SemanticPredictor
from fitness_app.motion_evidence import evaluate as motion_evaluate
from fitness_app.research_extract import atomic_json
from scripts.train_chest_expert import read,sha,OUT,OLD
from scripts.partial_action_training import source_weights,allowed_mask
from scripts.train_semantic_fusion import metrics_for_predictions
from scripts.encoder_adaptation_data import inputs,teachers,controls,pixels,fingerprint

CONFIG={'epochs':4,'seed':20261024,'backbone_lr':1e-5,'head_lr':1e-4,'weight_decay':.01,'microbatch':2,'effective_batch':8,
        'distillation_weight':.2,'anchor_strength':.001,'frames':8,'augmentation':'none; exact reviewed frames, full-frame fit; eval-mode dropout for fixed teacher alignment',
        'minimum_score':.8,'minimum_margin':.25,'low_score_rescue':.7,'selection':'final fixed fourth epoch; no validation used for stopping, tuning or epoch selection'}
CANDIDATE=OUT/'domain-encoder-adapter-candidate.pt'
PROGRESS=OUT/'domain-encoder-adapter-progress.pt'
REPORT=OUT/'domain-encoder-adapter-training.json'


def save_checkpoint(path,value):
    path.resolve().relative_to(ROOT);part=path.with_suffix('.pt.part');owned=False
    try:
        with part.open('xb') as f:owned=True;torch.save(value,f)
        part.replace(path)
    finally:
        if owned and part.exists():part.unlink()


def overlay(net,selected,checkpoint):
    state=checkpoint['state_dict']
    if set(state)!=set(selected):raise ValueError('编码器增量参数范围不同')
    for name,value in state.items():
        if value.shape!=selected[name].shape or not torch.isfinite(value).all():raise ValueError('编码器增量参数损坏')
    net.load_state_dict(state,strict=False)
    for name,value in state.items():
        if not torch.equal(selected[name].detach().cpu(),value):raise ValueError('参数保存回读不一致')


def fit(adviser,training,teacher,identity):
    if adviser.net is None:adviser._load()
    net=adviser.net;selected=configure(net);frozen=[name for name,value in net.named_parameters() if not value.requires_grad]
    frozen_before=parameter_sha(net,frozen);initial={name:value.detach().clone() for name,value in selected.items()}
    before={p:parameter_sha(net,[n for n in selected if n.startswith(p)]) for p in ('encoder.vision_model.encoder.layers.11.','encoder.mit.','head.')}
    groups=[{'params':[v for n,v in selected.items() if not n.startswith('head.')],'lr':CONFIG['backbone_lr']},
            {'params':[v for n,v in selected.items() if n.startswith('head.')],'lr':CONFIG['head_lr']}]
    optimizer=torch.optim.AdamW(groups,weight_decay=CONFIG['weight_decay']);history=[];start_epoch=0;prior_seconds=0.;steps=0
    if PROGRESS.exists():
        previous=torch.load(PROGRESS,map_location='cpu',weights_only=True)
        if previous['identity']!=identity or previous['frozen_before']!=frozen_before:raise ValueError('训练进度属于不同输入或冻结参数，不覆盖')
        overlay(net,selected,previous);optimizer.load_state_dict(previous['optimizer']);history=previous['history'];start_epoch=previous['completed_epochs'];prior_seconds=previous['elapsed_seconds'];steps=previous['optimizer_steps']
    # 原像素只在本进程内存中复用，不写RGB缓存。
    verified={};training_pixels=np.asarray([pixels(r,adviser.config,verified) for r in training],np.float16)
    labels=adviser.labels;mask=torch.tensor(allowed_mask(training,labels,True),dtype=torch.bool,device='cuda');weights=torch.tensor(source_weights(training),dtype=torch.float32,device='cuda');reference=torch.tensor(teacher,dtype=torch.float32,device='cuda')
    protected=(reference.max(1).values>=.8)&mask[torch.arange(len(training),device='cuda'),reference.argmax(1)]
    begin=time.perf_counter();torch.cuda.reset_peak_memory_stats();net.eval()
    print(json.dumps({'stage':'encoder_fit','training':len(training),'trainable_parameters':sum(v.numel() for v in selected.values()),'trainable_tensors':len(selected),'start_epoch':start_epoch,'epochs':4,'protected_teacher_samples':int(protected.sum())}),flush=True)
    for epoch in range(start_epoch,CONFIG['epochs']):
        order=list(range(len(training)));random.Random(CONFIG['seed']+epoch).shuffle(order);total_ce=total_kl=0.;epoch_steps=0
        for offset in range(0,len(order),CONFIG['effective_batch']):
            chunk=order[offset:offset+CONFIG['effective_batch']];optimizer.zero_grad(set_to_none=True)
            for micro in range(0,len(chunk),CONFIG['microbatch']):
                indices=chunk[micro:micro+CONFIG['microbatch']];frames=torch.from_numpy(training_pixels[indices]).cuda().float()
                with torch.autocast('cuda',dtype=torch.bfloat16):logits=net(frames).float()
                ce,kl,_=loss_terms(logits,mask[indices],weights[indices],reference[indices]);loss=(ce.sum()+CONFIG['distillation_weight']*kl.sum())/len(chunk)
                if not torch.isfinite(loss):raise ValueError('视频编码器适配损失非有限')
                loss.backward();total_ce+=float(ce.detach().sum());total_kl+=float(kl.detach().sum())
            anchor=CONFIG['anchor_strength']*sum((value-initial[name]).square().sum() for name,value in selected.items())/2
            if not torch.isfinite(anchor):raise ValueError('编码器锚定损失非有限')
            anchor.backward();torch.nn.utils.clip_grad_norm_(list(selected.values()),1.)
            if any(v.grad is not None for n,v in net.named_parameters() if n in frozen):raise ValueError('冻结视频前层出现梯度')
            if not any(v.grad is not None and torch.count_nonzero(v.grad)>0 for n,v in selected.items() if n.startswith('encoder.vision_model.encoder.layers.11.')):raise ValueError('视觉编码器没有实际训练梯度')
            optimizer.step();steps+=1;epoch_steps+=1
        result={'epoch':epoch+1,'classification_loss':total_ce/len(training),'distillation_loss':total_kl/len(training),'last_anchor':float(anchor.detach()),'optimizer_steps':epoch_steps}
        history.append(result);elapsed=prior_seconds+time.perf_counter()-begin
        save_checkpoint(PROGRESS,{'identity':identity,'state_dict':{n:v.detach().cpu().clone() for n,v in selected.items()},'optimizer':optimizer.state_dict(),'completed_epochs':epoch+1,'history':history,'frozen_before':frozen_before,'elapsed_seconds':elapsed,'optimizer_steps':steps})
        print(json.dumps({'stage':'encoder_epoch',**result,'elapsed_seconds':round(elapsed,1)}),flush=True)
    frozen_after=parameter_sha(net,frozen)
    if frozen_after!=frozen_before:raise ValueError('冻结前层参数实际改变')
    after={p:parameter_sha(net,[n for n in selected if n.startswith(p)]) for p in before}
    if any(before[p]==after[p] for p in before):raise ValueError('视觉末层/时序/头没有全部实际更新')
    fit_info={'training_runs':1,'completed_epochs':4,'optimizer_steps':steps,'history':history,'elapsed_seconds':prior_seconds+time.perf_counter()-begin,'frozen_before':frozen_before,'frozen_after':frozen_after,
              'component_before':before,'component_after':after,'trainable_tensors':len(selected),'trainable_parameters':sum(v.numel() for v in selected.values()),'protected_teacher_samples':int(protected.sum()),
              'gpu':torch.cuda.get_device_name(0),'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/2**20,1)}
    checkpoint={'identity':identity,'state_dict':{n:v.detach().cpu().clone() for n,v in selected.items()},'fit':fit_info,'runtime_enabled':False}
    save_checkpoint(CANDIDATE,checkpoint);reloaded=torch.load(CANDIDATE,map_location='cpu',weights_only=True);overlay(net,selected,reloaded)
    if PROGRESS.exists():
        current=torch.load(PROGRESS,map_location='cpu',weights_only=True)
        if current['identity']!=identity:raise ValueError('待清理进度身份改变')
        PROGRESS.unlink()
    return checkpoint


def main(verify=False,plan_only=False):
    torch.set_num_threads(4);torch.manual_seed(CONFIG['seed']);np.random.seed(CONFIG['seed'])
    training,evaluation,routed,parent,expected,observed=inputs();trained,manifest=verify_resources();predictor=SemanticPredictor()
    if parent['identity']['primary_weight_sha256']!=predictor.info['sha256'] or parent['identity']['primary_manifest_sha256']!=sha(ROOT/'data/models/semantic-action-classifier.json'):raise ValueError('父候选主干或策略改变')
    identity={'schema_version':1,'config':CONFIG,'base_checkpoint_sha256':manifest['sha256'],'primary_manifest_sha256':sha(ROOT/'data/models/semantic-action-classifier.json'),
              'parent_report_sha256':sha(OUT/'domain-adviser-evaluation.json'),'extra_review_sha256':sha(OUT/'encoder-adaptation-inputs.json'),
              'training':[{k:r[k] for k in ('id','source_sha256','group','start_ms','end_ms','crop','actual_ms','label_scope')} for r in training],
              'evaluation_ids':[r['id'] for r in evaluation],'routed':[{k:r[k] for k in ('id','source_sha256','group','actual_ms')} for r in routed],'scope':sorted(SCOPE),
              'code':{p:sha(ROOT/p) for p in ('scripts/train_video_encoder_adapter.py','scripts/encoder_adaptation_data.py','fitness_app/video_encoder_adaptation.py','fitness_app/video_sampling.py')}}
    plan_path=OUT/'domain-encoder-adapter-plan.json'
    if plan_path.exists():
        if read(plan_path)!=identity:raise ValueError('已冻结编码器适配计划不同，禁止按结果调整')
    else:atomic_json(plan_path,identity)
    print(json.dumps({'stage':'fixed_encoder_plan','training':len(training),'evaluation':len(evaluation),'routed':len(routed),'epochs':4}),flush=True)
    if plan_only:return
    adviser=DomainVideoAdviser(trained,manifest,research=True);new_fits=0;new_evaluation_calls=0
    try:
        teacher,teacher_info=teachers(training,adviser,verify=verify);control,control_info=controls(routed,adviser,verify=verify);n=len(training)
        if CANDIDATE.exists():
            checkpoint=torch.load(CANDIDATE,map_location='cpu',weights_only=True)
            if checkpoint['identity']!=identity:raise ValueError('编码器候选与固定计划不同')
            if verify and sha(CANDIDATE)!=read(REPORT)['candidate_sha256']:raise ValueError('已完成候选摘要改变')
        else:
            if verify:raise ValueError('未训练编码器候选，不在核查模式自动训练')
            checkpoint=fit(adviser,training,teacher[:n],identity);new_fits=1
        candidate_sha=sha(CANDIDATE);cache_path=OUT/'domain-encoder-adapter-output-cache.json';cache={'schema_version':1,'candidate_sha256':candidate_sha,'plan_sha256':sha(plan_path),'rows':[]}
        if cache_path.exists():
            cache=read(cache_path)
            if cache['candidate_sha256']!=candidate_sha or cache['plan_sha256']!=sha(plan_path):raise ValueError('新编码器输出缓存与参数/计划不同')
        cached={r['id']:r for r in cache['rows']};hits=0;verified={};by_parent={r['id']:r['candidate'] for r in parent['rows']};routed_index={r['id']:i for i,r in enumerate(routed)};outputs=[];overlaid=bool(new_fits)
        for row in evaluation:
            key=row['id'];base=by_parent[key];values={'unadapted':dict(base),'adapted':dict(base)};details={}
            if key in routed_index:
                i=routed_index[key];entry=routed[i];record_identity={k:entry[k] for k in ('id','source_sha256','start_ms','end_ms','crop','actual_ms')}
                if key in cached:
                    record=cached[key]
                    if record['identity']!=record_identity:raise ValueError('新编码器缓存输入改变')
                    probability=np.asarray(record['probability'],np.float32);hits+=1
                else:
                    if verify:raise ValueError('核查模式缺少新编码器输出，不自动调用GPU')
                    if not overlaid:
                        if adviser.net is None:adviser._load()
                        selected=configure(adviser.net);overlay(adviser.net,selected,checkpoint);overlaid=True
                    batch=pixels(entry,adviser.config,verified)
                    with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):probability=adviser.net(torch.from_numpy(batch[None]).cuda().float()).float().softmax(-1)[0].cpu().numpy()
                    new_evaluation_calls+=1;record={'id':key,'identity':record_identity,'probability':probability.tolist(),'probability_sha256':hashlib.sha256(probability.tobytes()).hexdigest()};cached[key]=record;cache['rows']=list(cached.values());atomic_json(cache_path,cache)
                if probability.shape!=(len(adviser.labels),) or not np.isfinite(probability).all() or np.any(probability<0) or not np.isclose(probability.sum(),1,atol=1e-5) or hashlib.sha256(probability.tobytes()).hexdigest()!=record['probability_sha256']:raise ValueError('新编码器输出缓存分布损坏')
                pose=project_path(observed[key]['pose_cache']) if key in observed else OLD/(key+'-full-world-features.npz')
                if sha(pose)!=expected[key]['pose_cache_sha256']:raise ValueError('原运动依据改变')
                with np.load(pose,allow_pickle=False) as a:arrays={k:a[k].copy() for k in ('timestamps','points','sizes','people')}
                order=np.argsort(probability)[::-1];new_score=float(probability[order[0]])
                proposals={'unadapted':control[key],'adapted':{'top':adviser.labels[order[0]],'score':new_score,'margin':new_score-float(probability[order[1]])}}
                for label,proposal in proposals.items():
                    top,score,margin=(proposal[k] for k in ('top','score','margin'))
                    motion=motion_evaluate(arrays,entry['actual_ms'][0],entry['actual_ms'][-1],top,predictor.info['motion_evidence']['config'])
                    values[label]=apply_prediction(base,top,score,margin,motion,'experimental-encoder-'+candidate_sha[:12]+'-'+label);details[label]={'top':top,'score':score,'margin':margin,'motion':motion}
            outputs.append({k:row[k] for k in ('id','source_sha256','group','partition')}|{'expected':row['action_id'],'parent':base,**values,'details':details,'changed':values['adapted']['action_id']!=base['action_id']})
        results={}
        for part in ('validation','diagnostic'):
            subset=[r for r in outputs if r['partition']==part];results[part]={}
            for name in ('parent','unadapted','adapted'):
                facts=[{'id':r['id'],'source_sha256':r['source_sha256'],'group':r['group'],'expected':r['expected'],'predicted':r[name]['action_id'],'top':r[name].get('top_action_id') or 'unknown','score':r[name]['score'],'margin':r[name]['margin']} for r in subset]
                results[part][name]=metrics_for_predictions(facts)
        after=results['validation']['adapted'];before=results['validation']['parent'];failures=[]
        if round(before['accuracy_including_rejections']*193)!=170:raise ValueError('父候选原成绩不同')
        for action,value in after['per_class'].items():
            prior=before['per_class'][action]
            if value['recall'] is None or value['recall']<.8:failures.append(action+': below80 or missing')
            if value['recall'] is not None and prior['recall'] is not None and value['recall']<prior['recall']:failures.append(action+': regression')
            if value['original_videos']<3 or value['groups']<2:failures.append(action+': source coverage insufficient')
        if after['negative_false_accepts']>before['negative_false_accepts']:failures.append('negative false accepts increased')
        if results['diagnostic']['adapted']['accuracy_including_rejections']<results['diagnostic']['parent']['accuracy_including_rejections']:failures.append('diagnostic regression')
        failures.append('independent error truth/real AI/mobile/full R3 acceptance pending')
        report={'schema_version':1,'scope':'last visual block, temporal MIT, visual projection and classification head adapted; complete original33 population and controls',
                'identity':{'plan_sha256':sha(plan_path),'base_checkpoint_sha256':manifest['sha256'],'primary_weight_sha256':predictor.info['sha256']},'fit':checkpoint['fit'],'candidate_sha256':candidate_sha,
                'candidate_path':CANDIDATE.relative_to(ROOT).as_posix(),'teacher_cache':teacher_info,'control_cache':control_info,'new_training_runs':new_fits,'new_evaluation_calls':new_evaluation_calls,'evaluation_cache_hits':hits,
                'training_inputs':n,'all_evaluation':len(evaluation),'routed_evaluation':len(routed),'results':results,'rows':outputs,'gate_passed':not failures,'gate_failures':failures,'runtime_enabled':False,
                'new_pose_inferences':0,'new_qwen_inferences':0,'serialization_tensors_verified':len(checkpoint['state_dict']),'output_cache_sha256':sha(cache_path),
                'limitations':['All validation here was already used for development, not a new independent test.','Two new hammer windows share an existing training publisher; no new independent actor claim.','Classification labels are not independent posture-correction truth.']}
        if REPORT.exists():
            previous=read(REPORT);report['initial_run']=previous.get('initial_run') or {k:previous[k] for k in ('teacher_cache','control_cache','new_training_runs','new_evaluation_calls','evaluation_cache_hits')}
        atomic_json(REPORT,report)
        print(json.dumps({'correct':{k:round(results['validation'][k]['accuracy_including_rejections']*193) for k in ('parent','unadapted','adapted')},'face_pull':after['per_class']['cable_face_pull'],'hammer':after['per_class']['hammer_curl'],'changed':sum(r['changed'] for r in outputs),'new_fit':new_fits,'new_evaluation_calls':new_evaluation_calls,'gate_passed':report['gate_passed'],'runtime_enabled':False},ensure_ascii=False),flush=True)
    finally:adviser.close()


if __name__=='__main__':
    if sys.argv[1:]==['--plan-only']:main(plan_only=True)
    elif sys.argv[1:]==['--verify-candidate']:main(True)
    elif sys.argv[1:]:raise SystemExit('仅支持无参数固定训练、--plan-only或--verify-candidate')
    else:main()
