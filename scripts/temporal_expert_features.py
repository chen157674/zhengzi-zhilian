"""复用同8帧的X-CLIP向量；仅缺项编码，不向编码器传动作名或来源标签。"""
import hashlib,inspect,json,importlib.metadata,time
from pathlib import Path
import av,numpy as np
from fitness_app.paths import ROOT,project_path
from fitness_app.media import display_rgb
from fitness_app.gym_recognition import PROMPTS
from scripts.evaluate_xclip import DIRECTORY,CONFIG,preprocess
from scripts.review_curl_training_inputs import decode

OUT=ROOT/'data/verification/round3';OLD=ROOT/'data/verification/step-22'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def signature(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def normalized(vector):
    value=np.asarray(vector,np.float32)
    length=float(np.linalg.norm(value))
    if value.shape!=(512,) or not np.isfinite(value).all() or not np.isfinite(length) or length<1e-8:raise ValueError('时序向量无效')
    return value/length


class Encoder:
    def __init__(self,config):self.model=None;self.config=config;self.calls=0;self.elapsed=[]
    def encode(self,images):
        import torch
        if self.model is None:
            from transformers import XCLIPModel
            if not torch.cuda.is_available():raise ValueError('当前实验需要既有CUDA设备，不自动换编码器')
            torch.set_num_threads(4);self.model=XCLIPModel.from_pretrained(DIRECTORY,local_files_only=True,dtype=torch.float16,attn_implementation='eager').cuda().eval()
        pixels=np.stack([preprocess(rgb,self.config) for rgb in images]);start=time.perf_counter()
        with torch.inference_mode():
            output=self.model.get_video_features(pixel_values=torch.from_numpy(pixels[None]).cuda().half())
            value=output.pooler_output;value=value/value.norm(p=2,dim=-1,keepdim=True);value=value[0].float().cpu().numpy()
        torch.cuda.synchronize();self.elapsed.append((time.perf_counter()-start)*1000);self.calls+=1
        return normalized(value)
    def close(self):
        if self.model is not None:
            import gc,torch
            self.model=None;gc.collect();torch.cuda.empty_cache()


def exact_images(row,desired):
    result=[]
    for stamp,rgb in decode(row,desired):
        if stamp!=desired[len(result)]:raise ValueError('实际解码时刻不是冻结语义帧，不用相邻帧替代')
        if row.get('crop'):
            h,w=rgb.shape[:2];l,t,r,b=row['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
        result.append(rgb)
    return result


def sequential_exact_images(row,desired):
    result=[]
    with av.open(str(project_path(row['path']))) as video:
        for frame in video.decode(video=0):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<desired[len(result)]:continue
            if stamp!=desired[len(result)]:raise ValueError('顺序解码也没有指定的真实时刻，拒绝替帧')
            rgb=display_rgb(frame)
            if row.get('crop'):
                h,w=rgb.shape[:2];l,t,r,b=row['crop'];rgb=np.ascontiguousarray(rgb[round(t*h):round(b*h),round(l*w):round(r*w)])
            result.append(rgb)
            if len(result)==len(desired):return result
    raise ValueError('顺序解码未取得全部冻结画面')


def recovering_images(row,desired):
    try:return exact_images(row,desired),False
    except ValueError:
        try:return sequential_exact_images(row,desired),True
        except ValueError as error:raise ValueError(row['id']+': '+str(error)) from error


def decoder_only_upgrade(previous,current):
    added={'sequential_exact_sha256','recovery_sha256'}
    return not (added&set(previous)) and added<=set(current) and previous=={k:v for k,v in current.items() if k not in added}


def legacy_vector(row,desired,config,model_info):
    path=OLD/(row['id']+'-xclip.npz')
    if not path.exists():return None
    expected=signature({'source_sha256':row['source_sha256'],'start_ms':row['start_ms'],'end_ms':row['end_ms'],'crop':row['crop'],
        'model_revision':model_info['revision'],'config':CONFIG,'preprocessor':config,'prompts':PROMPTS})
    with np.load(path,allow_pickle=False) as a:
        if str(a['signature'])!=expected:raise ValueError('既有X-CLIP缓存签名不同，不能冒充相同特征')
        stamps=a['stamps'];features=a['features'];indices=[i for i,t in enumerate(stamps) if np.array_equal(t,desired)]
        if not indices:return None
        value=normalized(features[indices[0]])
    with av.open(str(project_path(row['path']))) as video:
        stream=video.streams.video[0];first=next(video.decode(stream))
        if first.rotation not in (None,0) or stream.sample_aspect_ratio not in (None,1):return None
    return value,{'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'view':indices[0]}


def load_temporal(rows,requested,expected,observed):
    info=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    if info['status']!='verified':raise ValueError('时序编码器未核验')
    for name,meta in info['files'].items():
        if sha(project_path(DIRECTORY/name))!=meta['sha256']:raise ValueError('时序编码器文件变化')
    config=json.loads((DIRECTORY/'preprocessor_config.json').read_text(encoding='utf-8'))
    if isinstance(config['size'],int):config['size']={'shortest_edge':config['size']}
    config.setdefault('crop_size',224)
    if isinstance(config['crop_size'],int):config['crop_size']={'width':config['crop_size'],'height':config['crop_size']}
    config.setdefault('rescale_factor',1/255)
    policy={'schema':'exact-eight-frame-temporal-expert-1','model_revision':info['revision'],'manifest_sha256':sha(DIRECTORY/'manifest.json'),'preprocessor':config,
        'preprocess_sha256':hashlib.sha256(inspect.getsource(preprocess).encode()).hexdigest(),'display_sha256':hashlib.sha256(inspect.getsource(display_rgb).encode()).hexdigest(),
        'decode_sha256':hashlib.sha256(inspect.getsource(decode).encode()).hexdigest(),'pixel_assembly_sha256':hashlib.sha256(inspect.getsource(exact_images).encode()).hexdigest(),
        'normalizer_sha256':hashlib.sha256(inspect.getsource(normalized).encode()).hexdigest(),'encoder_sha256':hashlib.sha256(inspect.getsource(Encoder.encode).encode()).hexdigest(),
        'sequential_exact_sha256':hashlib.sha256(inspect.getsource(sequential_exact_images).encode()).hexdigest(),'recovery_sha256':hashlib.sha256(inspect.getsource(recovering_images).encode()).hexdigest(),
        'versions':{k:importlib.metadata.version(k) for k in ('torch','transformers','av','Pillow')},'input':'RGB only; no text, labels or metadata tokens'}
    policy_sha=signature(policy);path=OUT/'temporal-expert-features.npz';records={};vectors={};pilot=None;migrations=[];upgraded=False
    if path.exists():
        with np.load(path,allow_pickle=False) as a:
            header=json.loads(str(a['header']))
            if signature(header['policy'])!=header['policy_sha256']:raise ValueError('时序缓存策略自身摘要损坏')
            if header['policy_sha256']!=policy_sha:
                if not decoder_only_upgrade(header['policy'],policy):raise ValueError('时序缓存策略变化，不覆盖旧特征')
                upgraded=True
            data=a['vectors'].copy()
        if len(data)!=len(header['records']):raise ValueError('时序缓存行数不符')
        for record,value in zip(header['records'],data):
            if record['id'] in records or sha_bytes(value)!=record['feature_sha256'] or value.shape!=(512,) or not np.isfinite(value).all() or abs(float(np.linalg.norm(value))-1)>1e-5:raise ValueError('时序缓存向量损坏或重复')
            if record['identity']['policy_sha256']!=header['policy_sha256'] or signature(record['identity'])!=record['pixel_signature']:raise ValueError('时序缓存输入身份损坏')
            if upgraded:
                record['previous_pixel_signature']=record['pixel_signature'];record['identity']['policy_sha256']=policy_sha;record['pixel_signature']=signature(record['identity'])
            records[record['id']]=record;vectors[record['id']]=value.copy()
        pilot=header.get('legacy_api_check')
        migrations=header.get('metadata_migrations',[])
        if upgraded:migrations=migrations+[{'from':header['policy_sha256'],'to':policy_sha,'preserved_vectors':len(records),
            'basis':'only add sequential recovery for exact specified source frames; prior successful seek/crop/normalizer/encoder unchanged; vector bytes verified unchanged'}]
    encoder=Encoder(config);verified_media={};reused=hits=0;recoveries=[]
    def save():
        keys=list(records);header={'policy':policy,'policy_sha256':policy_sha,'records':[records[k] for k in keys],'legacy_api_check':pilot,'metadata_migrations':migrations}
        temporary=path.with_suffix('.npz.part');owned=False
        try:
            with temporary.open('xb') as f:owned=True;np.savez_compressed(f,header=json.dumps(header,sort_keys=True),vectors=np.asarray([vectors[k] for k in keys],np.float32))
            temporary.replace(path)
        finally:
            if owned and temporary.exists():temporary.unlink()
    try:
        if upgraded:save()
        for counter,index in enumerate(requested,1):
            row=rows[index];key=row['id'];semantic=project_path(observed[key]['semantic_cache']) if key in observed else project_path(row['cache'])
            if sha(semantic)!=expected[key]['semantic_cache_sha256']:raise ValueError('时序输入与已冻结语义帧不同')
            with np.load(semantic,allow_pickle=False) as a:desired=a['timestamps'].astype(np.int64).tolist()
            if len(desired)!=8 or any(b<=a for a,b in zip(desired,desired[1:])):raise ValueError('时序输入必须为8个实际递增时刻')
            identity={'source_sha256':row['source_sha256'],'start_ms':row['start_ms'],'end_ms':row['end_ms'],'crop':row['crop'],'actual_ms':desired,'policy_sha256':policy_sha}
            pixel_sha=signature(identity)
            if key in records:
                if records[key]['pixel_signature']!=pixel_sha:raise ValueError('同一输入的实际像素身份改变，不覆盖')
                hits+=1;continue
            media=project_path(row['path'])
            if row['path'] not in verified_media:verified_media[row['path']]=sha(media)
            if verified_media[row['path']]!=row['source_sha256']:raise ValueError('原视频摘要改变')
            legacy=legacy_vector(row,desired,config,info)
            if legacy is not None:
                value,legacy_info=legacy
                if pilot is None:
                    images,recovered=recovering_images(row,desired)
                    if recovered:recoveries.append(key)
                    check=encoder.encode(images);difference=float(np.max(abs(check-value)));cosine=float(check@value)
                    if difference>5e-4 or cosine<.9999:raise ValueError('新API与旧缓存的视觉向量不等价，不沿用旧向量')
                    pilot={'id':key,'maximum_absolute_difference':difference,'cosine':cosine,'maximum_allowed_difference':5e-4,'minimum_cosine':.9999,'new_encoder_calls':1}
                reused+=1
            else:
                images,recovered=recovering_images(row,desired)
                if recovered:recoveries.append(key)
                value=encoder.encode(images);legacy_info=None
            vectors[key]=value;records[key]={'id':key,'pixel_signature':pixel_sha,'identity':identity,'feature_sha256':sha_bytes(value),'legacy':legacy_info};save()
            if counter%10==0 or counter==len(requested):print(json.dumps({'stage':'temporal_features','ready':counter,'requested':len(requested),'cached':hits,'legacy_reused':reused,'new_encoder_calls':encoder.calls}),flush=True)
        matrix=np.asarray([vectors[rows[i]['id']] for i in requested],np.float32)
        return matrix,{'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'policy_sha256':policy_sha,'requested':len(requested),'cache_hits':hits,'legacy_reused':reused,
            'new_encoder_calls':encoder.calls,'legacy_api_check':pilot,'median_encoder_ms':float(np.median(encoder.elapsed)) if encoder.elapsed else None,
            'sequential_recoveries':recoveries,'metadata_migrations':migrations,'total_cached_native_encodings':sum(r.get('legacy') is None for r in records.values()),
            'records':[records[rows[i]['id']] for i in requested]}
    finally:encoder.close()


def sha_bytes(value):return hashlib.sha256(np.asarray(value,np.float32).tobytes()).hexdigest()
