"""比较固定X-CLIP视频编码器；每个输出来自8帧连续短视频，不把单帧当视频。"""
import argparse,hashlib,json,os,sys,time,collections
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np,torch
from fitness_app.gym_recognition import PROMPTS
from PIL import Image
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/step-22'
DIRECTORY=ROOT/'data/models/xclip-base-patch32'
CONFIG={'schema':'xclip-eight-frame-1','frames':8,'window_ms':3200,'views_per_segment':3,'templates':['A video of {}.','A person is performing this exercise: {}.']}


def preprocess(rgb,config):
    image=Image.fromarray(rgb);width,height=image.size;edge=config['size']['shortest_edge']
    size=(edge,int(edge*height/width)) if width<height else (int(edge*width/height),edge)
    image=image.resize(size,Image.Resampling(config['resample']))
    cw,ch=config['crop_size']['width'],config['crop_size']['height'];left,top=(size[0]-cw)//2,(size[1]-ch)//2
    values=np.asarray(image.crop((left,top,left+cw,top+ch)),np.float32)*np.float32(config['rescale_factor'])
    values=(values-np.asarray(config['image_mean'],np.float32))/np.asarray(config['image_std'],np.float32)
    return np.ascontiguousarray(values.transpose(2,0,1))


def sources(include_legacy=False):
    manifest=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))
    by={r['id']:r for r in manifest['sources']}
    rows=[]
    if include_legacy:
        old=json.loads((ROOT/'data/experiments/visual-action-classifier.json').read_text(encoding='utf-8'))
        for entry in old['identity']['inputs']:
            if entry['partition'] not in {'train','validation'}:continue
            with av.open(str(project_path(entry['path']))) as video:duration=round(float(video.duration)/1000)
            rows.append({**entry,'id':'legacy-'+hashlib.sha256(entry['key'].encode()).hexdigest()[:20],'action_id':entry['action'],
                         'source_sha256':entry['sha256'],'start_ms':0 if duration<2300 else 1000,'end_ms':min(9000,duration),'crop':None})
    for segment in json.loads((ROOT/'data/datasets/development-movements/gym-segments.json').read_text(encoding='utf-8'))['segments']:
        if segment['partition'] not in {'train','validation','diagnostic'}:continue
        rows.append({**segment,'path':by[segment['source_id']]['path']})
    from fitness_app.source_partitions import enforce_source_policy
    enforce_source_policy(rows)
    enforce_source_policy(rows,operation='inference')
    return rows


def pixels(path,start,end,crop,config):
    desired=np.linspace(start,end-100,8);images=[];stamps=[];index=0
    with av.open(str(path)) as video:
        video.seek(round(start)*1000)
        for frame in video.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<desired[index]:continue
            rgb=frame.to_ndarray(format='rgb24')
            if crop:
                h,w=rgb.shape[:2];l,t,r,b=crop;rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            images.append(preprocess(rgb,config));stamps.append(stamp);index+=1
            if index==8:break
    if len(images)!=8:raise ValueError('视频没有8个真实采样帧')
    return np.asarray(images,np.float32),stamps


def run(include_legacy=False,partition=None):
    from transformers import XCLIPModel
    from tokenizers import Tokenizer
    info=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    if info['status']!='verified':raise ValueError('XCLIP尚未完成摘要校验')
    for name,item in info['files'].items():
        if hashlib.sha256((DIRECTORY/name).read_bytes()).hexdigest()!=item['sha256']:raise ValueError('XCLIP资源改变')
    config=json.loads((DIRECTORY/'preprocessor_config.json').read_text(encoding='utf-8'))
    if isinstance(config['size'],int):config['size']={'shortest_edge':config['size']}
    config.setdefault('crop_size',224)
    if isinstance(config['crop_size'],int):config['crop_size']={'width':config['crop_size'],'height':config['crop_size']}
    config.setdefault('rescale_factor',1/255)
    labels=list(PROMPTS);texts=[t.format(description) for description in PROMPTS.values() for t in CONFIG['templates']]
    tokenizer=Tokenizer.from_file(str(DIRECTORY/'tokenizer.json'));tokenizer.enable_truncation(max_length=77)
    tokenizer.enable_padding(length=77,pad_id=tokenizer.token_to_id('<|endoftext|>'),pad_token='<|endoftext|>')
    encoded=tokenizer.encode_batch(texts);ids=torch.tensor([t.ids for t in encoded],device='cuda');mask=torch.tensor([t.attention_mask for t in encoded],device='cuda')
    model=None;latency=[];results=[];entries=sources(include_legacy)
    for i,entry in enumerate(entries):
        if partition and entry['partition']!=partition:continue
        signature=hashlib.sha256(json.dumps({'source_sha256':entry['source_sha256'],'start_ms':entry['start_ms'],'end_ms':entry['end_ms'],'crop':entry['crop'],
                                           'model_revision':info['revision'],'config':CONFIG,'preprocessor':config,'prompts':PROMPTS},sort_keys=True).encode()).hexdigest()
        target=OUT/(entry['id']+'-xclip.npz')
        if target.exists():
            with np.load(target,allow_pickle=False) as cache:
                if str(cache['signature'])!=signature:raise ValueError('XCLIP缓存身份改变')
                logits=cache['logits'].copy();features=cache['features'].copy()
        else:
            if model is None:model=XCLIPModel.from_pretrained(DIRECTORY,local_files_only=True,dtype=torch.float16,attn_implementation='eager').cuda().eval()
            start,end=entry['start_ms'],entry['end_ms'];span=min(CONFIG['window_ms'],end-start)
            ends=np.unique(np.linspace(start+span,end,CONFIG['views_per_segment']).round().astype(int));logits=[];features=[];stamps=[]
            for stop in ends:
                frames,times=pixels(project_path(entry['path']),stop-span,stop,entry['crop'],config)
                began=time.perf_counter()
                with torch.inference_mode():output=model(input_ids=ids,attention_mask=mask,pixel_values=torch.from_numpy(frames[None]).cuda().half())
                torch.cuda.synchronize();latency.append((time.perf_counter()-began)*1000)
                logits.append(output.logits_per_video[0].float().cpu().numpy().reshape(len(labels),len(CONFIG['templates'])).mean(1))
                features.append(output.video_embeds[0].float().cpu().numpy());stamps.append(times)
            logits=np.asarray(logits);features=np.asarray(features);part=target.with_suffix('.npz.part');owned=False
            try:
                with part.open('xb') as stream:
                    owned=True;np.savez_compressed(stream,signature=signature,logits=logits,features=features,stamps=stamps)
                os.replace(part,target)
            finally:
                if owned and part.exists():part.unlink()
        scores=logits.mean(0);order=np.argsort(scores)[::-1];prob=np.exp(scores-scores.max());prob/=prob.sum()
        top=labels[int(order[0])];score=float(prob[order[0]]);margin=score-float(prob[order[1]])
        row={k:entry[k] for k in ('id','action_id','partition','group')}
        row.update(top=top,predicted=top if score>=.7 and margin>=.2 and not top.startswith('unknown_') else None,
                   score=score,margin=margin,cache=target.relative_to(ROOT).as_posix(),score_is_calibrated_probability=False)
        results.append(row)
        if (i+1)%10==0:print(json.dumps({'ready':i+1,'total':len(entries),'last':row['id'],'expected':row['action_id'],'top':row['top']}),flush=True)
    validation=[r for r in results if r['partition']=='validation'];actions=sorted({r['action_id'] for r in validation})
    metrics={'clips':len(validation),'top1':sum(r['top']==r['action_id'] for r in validation)/max(1,len(validation)),
             'accuracy_including_rejections':sum(r['predicted']==r['action_id'] for r in validation)/max(1,len(validation)),
             'per_class':{a:{'clips':sum(r['action_id']==a for r in validation),'top1':sum(r['top']==a for r in validation if r['action_id']==a)/sum(r['action_id']==a for r in validation)} for a in actions}}
    atomic_json(OUT/'xclip-screening.json',{'schema_version':1,'model_revision':info['revision'],'config':CONFIG,'scope':'development source-separated video comparison; not posture-error truth',
                                         'metrics':metrics,'results':results,'latency_ms':{'p50':float(np.percentile(latency,50)) if latency else None,'p95':float(np.percentile(latency,95)) if latency else None},'runtime_enabled':False})
    print(json.dumps(metrics),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--include-legacy',action='store_true');parser.add_argument('--partition');args=parser.parse_args();run(args.include_legacy,args.partition)
