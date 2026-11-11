"""在33类健身动作上微调X-CLIP视频视觉编码器；不依赖生成式代码选择。"""
import argparse,collections,hashlib,json,random,sys,time,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np,torch
from torch import nn
from transformers import XCLIPModel
from fitness_app.catalogue import DEFINITIONS
from fitness_app.video_sampling import sample_frames,fit_pixels
from fitness_app.research_extract import atomic_json
from evaluate_xclip import sources,OUT,DIRECTORY

DESTINATION=ROOT/'data/models/xclip-fitness'
LABELS=['unknown']+[row[0] for row in DEFINITIONS]
CONFIG={'schema':'xclip-fitness-full-video-1','frames':8,'window_ms':3200,'pixels':'full_frame_fit_224_mean_padding_after_encoding_borders',
        'minimum_score':.5,'minimum_margin':.15,'negative_class':'unknown','backbone_lr':1e-5,'head_lr':.001,
        'sampler':'equal_class_then_equal_source_clip; uniform active-interval center; no original test or new holdout',
        'augmentation':'same brightness/contrast/flip/fit scale for all eight frames; no temporal order shuffle'}


class FitnessVideoNetwork(nn.Module):
    def __init__(self,classes):
        super().__init__()
        self.encoder=XCLIPModel.from_pretrained(DIRECTORY,local_files_only=True,dtype=torch.float32,attn_implementation='eager')
        # 本任务训练明确类别的视频分类，文本/提示生成器不参与，避免保存无用权重。
        for name in ('text_model','text_projection','prompts_visual_projection','prompts_visual_layernorm','prompts_generator','logit_scale'):
            if hasattr(self.encoder,name):delattr(self.encoder,name)
        self.head=nn.Sequential(nn.LayerNorm(512),nn.Dropout(.2),nn.Linear(512,classes))
    def forward(self,frames):return self.head(self.encoder.get_video_features(pixel_values=frames).pooler_output)


def summarise(entries,probabilities,labels):
    predictions=[]
    for entry,scores in zip(entries,probabilities):
        order=np.argsort(scores)[::-1];top=labels[int(order[0])];score=float(scores[order[0]]);margin=score-float(scores[order[1]])
        predicted=top if top!='unknown' and score>=CONFIG['minimum_score'] and margin>=CONFIG['minimum_margin'] else None
        predictions.append({'id':entry['id'],'source_group':entry['group'],'expected':entry['action_id'],'top':top,'predicted':predicted,'score':score,'margin':margin})
    positive=[r for r in predictions if r['expected']!='unknown'];negative=[r for r in predictions if r['expected']=='unknown'];classes={}
    for action in labels[1:]:
        cases=[r for r in positive if r['expected']==action];accepted=[r for r in positive if r['predicted']==action]
        classes[action]={'clips':len(cases),'groups':len({r['source_group'] for r in cases}),'recall':sum(r['predicted']==action for r in cases)/len(cases) if cases else None,
                         'top1':sum(r['top']==action for r in cases)/len(cases) if cases else None,
                         'precision':sum(r['expected']==action for r in accepted)/len(accepted) if accepted else None}
    return {'positive_clips':len(positive),'accuracy_including_rejections':sum(r['predicted']==r['expected'] for r in positive)/max(1,len(positive)),
            'coverage':sum(r['predicted'] is not None for r in positive)/max(1,len(positive)),
            'top1':sum(r['top']==r['expected'] for r in positive)/max(1,len(positive)),
            'macro_recall_all_33':sum(r['recall'] or 0 for r in classes.values())/33,
            'negative_clips':len(negative),'negative_false_accepts':sum(r['predicted'] is not None for r in negative),
            'per_class':classes,'predictions':predictions}


def run(epochs=8,per_class=20):
    seed=20261023;random.seed(seed);np.random.seed(seed);torch.manual_seed(seed);torch.cuda.manual_seed_all(seed);torch.set_num_threads(4)
    entries=sources(include_legacy=True);training=collections.defaultdict(list)
    for entry in entries:
        if entry['partition']=='train':training[entry['action_id']].append(entry)
    missing=set(LABELS)-set(training)
    if missing:raise ValueError('不能将未训练类冒充支持: '+str(sorted(missing)))
    validation=[r for r in entries if r['partition']=='validation']
    if {r['group'] for r in entries if r['partition']=='train'}&{r['group'] for r in validation}:raise ValueError('来源组交叉')
    config=json.loads((DIRECTORY/'preprocessor_config.json').read_text());mapping={a:i for i,a in enumerate(LABELS)}
    identity={'inputs':[{k:r[k] for k in ('id','source_sha256','start_ms','end_ms','crop','action_id','partition','group')} for r in entries],
              'config':CONFIG,'epochs':epochs,'samples_per_class_per_epoch':per_class,'seed':seed,'model_revision':json.loads((DIRECTORY/'manifest.json').read_text())['revision'],
              'code':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in ('scripts/train_xclip_fitness.py','fitness_app/video_sampling.py')}}
    signature=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest();report_path=OUT/'xclip-fitness-training.json'
    if report_path.exists():
        prior=json.loads(report_path.read_text(encoding='utf-8'))
        if prior.get('signature')==signature and (DESTINATION/'candidate.pt').exists() and hashlib.sha256((DESTINATION/'candidate.pt').read_bytes()).hexdigest()==prior.get('checkpoint_sha256'):
            print(json.dumps({'reused':True,'metrics':{k:v for k,v in prior['validation'].items() if k not in ('per_class','predictions')}}));return
    net=FitnessVideoNetwork(len(LABELS)).cuda();optimizer=torch.optim.AdamW([{'params':net.encoder.parameters(),'lr':CONFIG['backbone_lr']},{'params':net.head.parameters(),'lr':CONFIG['head_lr']}],weight_decay=.01)
    criterion=nn.CrossEntropyLoss(label_smoothing=.05);best=-1.;history=[];began=time.perf_counter()
    print(json.dumps({'train_clips':sum(map(len,training.values())),'validation_clips':len(validation),'classes':len(LABELS)-1,'trainable_parameters':sum(p.numel() for p in net.parameters()),'epochs':epochs}),flush=True)
    # 开发验证只缓存变换后的内存像素，不保留本机用户视频或图像文件。
    validation_pixels=[]
    for i,entry in enumerate(validation):
        pixels,_=sample_frames(entry);validation_pixels.append(fit_pixels(pixels,config).astype(np.float16))
        if (i+1)%50==0:print(json.dumps({'validation_pixels_ready':i+1}),flush=True)
    def evaluate():
        net.eval();result=[]
        with torch.inference_mode(),torch.autocast('cuda',dtype=torch.bfloat16):
            for offset in range(0,len(validation_pixels),4):
                batch=torch.from_numpy(np.asarray(validation_pixels[offset:offset+4])).cuda().float()
                result.extend(net(batch).float().softmax(-1).cpu().numpy())
        return summarise(validation,np.asarray(result),LABELS)
    for epoch in range(epochs):
        net.train();schedule=LABELS*per_class;random.shuffle(schedule);total=0.;steps=0
        for offset in range(0,len(schedule),2):
            batch=[];targets=[]
            for action in schedule[offset:offset+2]:
                entry=random.choice(training[action]);span=entry['end_ms']-entry['start_ms'];center=None
                if span>3400:center=random.uniform(entry['start_ms']+1600,entry['end_ms']-1600)
                images,_=sample_frames(entry,center=center)
                augmentation={'flip':random.random()<.5,'brightness':random.uniform(.85,1.15),'contrast':random.uniform(.85,1.15),'scale':random.uniform(.9,1.)}
                batch.append(fit_pixels(images,config,augmentation));targets.append(mapping[action])
            optimizer.zero_grad(set_to_none=True)
            with torch.autocast('cuda',dtype=torch.bfloat16):loss=criterion(net(torch.from_numpy(np.asarray(batch)).cuda()),torch.tensor(targets,device='cuda'))
            if not torch.isfinite(loss):raise ValueError('视频视觉训练损失非有限')
            loss.backward();torch.nn.utils.clip_grad_norm_(net.parameters(),1.);optimizer.step();total+=float(loss.detach());steps+=1
            if steps%100==0:print(json.dumps({'epoch':epoch+1,'step':steps,'loss':total/steps,'elapsed_seconds':round(time.perf_counter()-began,1)}),flush=True)
        metrics=evaluate();history.append({'epoch':epoch+1,'train_loss':total/steps,'metrics':{k:v for k,v in metrics.items() if k not in ('per_class','predictions')}})
        print(json.dumps(history[-1]),flush=True)
        score=metrics['macro_recall_all_33']-.1*metrics['negative_false_accepts']
        if score>best:
            best=score;DESTINATION.resolve().relative_to(ROOT);DESTINATION.mkdir(exist_ok=True)
            destination=DESTINATION/'candidate.pt';part=destination.with_suffix('.pt.part')
            with part.open('xb') as stream:torch.save({'state_dict':{k:v.detach().cpu() for k,v in net.state_dict().items()},'labels':LABELS,'config':CONFIG,'signature':signature},stream)
            os.replace(part,destination)
            digest=hashlib.sha256(destination.read_bytes()).hexdigest()
            passed=bool(metrics['accuracy_including_rejections']>=.7 and metrics['macro_recall_all_33']>=.7 and metrics['coverage']>=.8 and
                        all(r['recall'] is not None and r['recall']>=.7 for r in metrics['per_class'].values()) and metrics['negative_false_accepts']==0)
            report={'schema_version':1,'signature':signature,'identity':identity,'validation':metrics,'history':history,'checkpoint_sha256':digest,
                    'model':'supervised eight-frame XCLIP visual encoder + temporal integration + 34-way exercise/background head',
                    'runtime_enabled':False,'development_gate_passed':passed,'test_used':False,'specific_error_validation_passed':False,
                    'hardware':{'gpu':torch.cuda.get_device_name(0),'peak_allocated_mib':round(torch.cuda.max_memory_allocated()/2**20,1)},
                    'selection':'development macro recall with background false-positive penalty; not blind heldout result'}
            atomic_json(report_path,report)
            atomic_json(DESTINATION/'manifest.json',{'signature':signature,'labels':LABELS,'config':CONFIG,'sha256':digest,'runtime_enabled':False,'development_gate_passed':passed})
    print(json.dumps({'finished':True,'epochs':epochs,'best_score':best,'seconds':round(time.perf_counter()-began,1)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--epochs',type=int,default=8);parser.add_argument('--per-class',type=int,default=20);args=parser.parse_args();run(args.epochs,args.per_class)
