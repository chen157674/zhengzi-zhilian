"""按真实俯身阶段选图的冻结CLIP来源条件对照；先核图再拟合。"""
import argparse,hashlib,json,math,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import av,cv2,numpy as np
from PIL import Image,ImageDraw
import onnxruntime as ort
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
import joblib
from fitness_app.research_extract import atomic_json
from fitness_app.media import display_rgb,source_stamp
from fitness_app.deadlift_visual import torso_geometry
from fitness_app.visual_recognition import DIRECTORY,normalize
from scripts.train_visual_posture import cases,sha
from scripts.train_deadlift_condition import condition_metrics,release_gate
from scripts.partial_action_training import assert_source_split,source_weights
REPORT=ROOT/'data/verification/round3/deadlift-clip-hinge-training.json'
CACHE=ROOT/'data/experiments/features/deadlift-clip-hinge.npz'
REVIEW=ROOT/'data/verification/round3/deadlift-clip-hinge-inputs.jpg'
DEST=ROOT/'data/models/open-source-correction/deadlift-clip-hinge-head'
CONFIG={'seed':20261103,'C':1.,'max_iter':1000,'minimum_score':.8,'maximum_hip_degrees':150.,
    'minimum_trunk_tilt':20.,'selected_tilt_fraction':1/3,'frames':8,'channels':'original_gray_torso',
    'mirror':'average_l2_original_and_flip','pool':['mean','std_ddof_0'],'backbone_frozen':True}


def choose(points,sizes,counts,accepted):
    candidates=[]
    for i,(p,size,count,identity) in enumerate(zip(points,sizes,counts,accepted)):
        width,height=map(int,size)
        matrix=torso_geometry(p,width,height,int(count),bool(identity))
        if matrix is None:continue
        visible=[]
        for offset in (0,1):
            joints=p[[11+offset,23+offset,25+offset]]
            if not np.isfinite(joints).all() or np.min(joints[:,3:5])<.5:continue
            shoulder,hip,knee=joints[:,:2]*size;u=shoulder-hip;v=knee-hip
            den=np.linalg.norm(u)*np.linalg.norm(v)
            if den<1e-6:continue
            angle=math.degrees(math.acos(float(np.clip(u@v/den,-1,1))))
            tilt=math.degrees(math.acos(float(np.clip(-u[1]/np.linalg.norm(u),-1,1))))
            visible.append((float(np.min(joints[:,3:5])),angle,tilt))
        if not visible:continue
        confidence,angle,tilt=max(visible)
        if angle<=CONFIG['maximum_hip_degrees'] and tilt>=CONFIG['minimum_trunk_tilt']:
            candidates.append((i,tilt,matrix,angle))
    if len(candidates)<8:return [],'insufficient_hinge_frames'
    threshold=float(np.quantile([row[1] for row in candidates],1-CONFIG['selected_tilt_fraction']))
    deepest=[row for row in candidates if row[1]>=threshold]
    if len(deepest)<8:return [],'insufficient_deep_hinge_frames'
    return [deepest[i] for i in np.linspace(0,len(deepest)-1,8).round().astype(int)],None


def prepare():
    if REPORT.exists() or CACHE.exists() or REVIEW.exists() or DEST.exists():raise ValueError('原俯身对照已存在，不覆盖或重复启动')
    rows=cases();original=json.loads((ROOT/'data/verification/round3/deadlift-image-adapter-training.json').read_text(encoding='utf-8'))
    by={r['id']:r for r in original['inputs']}
    manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    for name in ('onnx/vision_model_quantized.onnx','preprocessor_config.json'):
        if sha(DIRECTORY/name)!=manifest['files'][name]['sha256']:raise ValueError('CLIP资源变化')
    protocol={'config':CONFIG,'script_sha256':sha(Path(__file__)),'source_index_sha256':sha(ROOT/'data/datasets/index.csv'),
        'original_input_report_sha256':sha(ROOT/'data/verification/round3/deadlift-image-adapter-training.json'),
        'backbone_sha256':manifest['files']['onnx/vision_model_quantized.onnx']['sha256']}
    document={'state':'encoding','pid':os.getpid(),'protocol':protocol,'inputs':[],
        'scope':'publisher_GB_PB_hinge_image_condition_not_frame_error_truth','production_enabled':False,
        'test_media_or_features_read':False,'private_media_uploaded':False,'new_pose_inferences':0,'new_encoded_clips':0,'fits':0}
    atomic_json(REPORT,document);began=time.perf_counter()
    try:
        processor=json.loads((DIRECTORY/'preprocessor_config.json').read_text(encoding='utf-8'))
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        session=ort.InferenceSession(str(DIRECTORY/'onnx/vision_model_quantized.onnx'),options,providers=['CPUExecutionProvider'])
        features=np.full((97,1024),np.nan,np.float32);examples={};examples_seen={}
        for index,row in enumerate(rows):
            old=by[row['id']];record={k:row[k] for k in ('id','source_subject','source_label','source_sha256','condition_fold')}
            record['reason']=old['reason']
            if old['reason']:document['inputs'].append(record);continue
            path=ROOT/row['path'];pose=ROOT/old['pose_cache']
            if sha(path)!=row['source_sha256'] or sha(pose)!=old['pose_sha256']:raise ValueError('实际原片或姿态摘要变化')
            with np.load(pose,allow_pickle=False) as z:
                stamps=z['timestamps_ms'];selected,reason=choose(z['points'],z['sizes'],z['person_counts'],z['identity_accepted'])
            record['reason']=reason
            if reason:document['inputs'].append(record);continue
            wanted={r[0]:r for r in selected};patches=[];times=[]
            with av.open(str(path),options={'protocol_whitelist':'file'}) as container:
                stream=container.streams.video[0];origin=round(float(stream.start_time*stream.time_base)*1000) if stream.start_time is not None else 0
                for i,frame in enumerate(container.decode(stream)):
                    if i not in wanted:continue
                    stamp=source_stamp(frame,origin)
                    if stamp!=int(stamps[i]):raise ValueError('阶段选帧源时间不符')
                    rgb=display_rgb(frame);patch=cv2.warpAffine(rgb,wanted[i][2],(224,224),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT,borderValue=(127,127,127))
                    patches.append(cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY));times.append(stamp)
                    if len(patches)==8:break
            if len(patches)!=8 or len(set(times))!=8:raise ValueError('必须八张不同真实源帧')
            gray=np.stack(patches);record.update(sampled_source_ms=times,gray_patch_sha256=hashlib.sha256(gray.tobytes()).hexdigest(),
                trunk_tilt_degrees=[r[1] for r in selected],hip_degrees=[r[3] for r in selected])
            encoded=[]
            for frame in gray:
                variants=[]
                for image in (frame,frame[:,::-1]):
                    value=np.repeat(image[:,:,None],3,axis=2).astype(np.float32)*np.float32(processor['rescale_factor'])
                    value=(value-np.asarray(processor['image_mean'],np.float32))/np.asarray(processor['image_std'],np.float32)
                    output=session.run(['image_embeds'],{'pixel_values':np.ascontiguousarray(value.transpose(2,0,1)[None])})[0]
                    if output.shape!=(1,512) or not np.isfinite(output).all():raise ValueError('CLIP输出结构无效')
                    variants.append(normalize(output)[0])
                encoded.append(np.mean(variants,axis=0))
            encoded=np.asarray(encoded);features[index]=np.concatenate((encoded.mean(0),encoded.std(0)))
            # 每个人物／条件取首段，选择只影响核对图，不影响拟合清单。
            key=(row['source_subject'],row['source_label'])
            if key not in examples:examples[key]=(gray.copy(),times,row['id'])
            document['inputs'].append(record);document['new_encoded_clips']+=1
            if document['new_encoded_clips']%10==0:atomic_json(REPORT,document);print(json.dumps({'hinge_encoded':document['new_encoded_clips']}),flush=True)
        sheet=Image.new('RGB',(8*144, len(examples)*170),(245,245,245));draw=ImageDraw.Draw(sheet)
        for y,(key,(gray,times,identity)) in enumerate(sorted(examples.items())):
            for x,(image,stamp) in enumerate(zip(gray,times)):
                sheet.paste(Image.fromarray(image).convert('RGB').resize((144,144)),(x*144,y*170))
                draw.text((x*144+2,y*170+145),f'{key[0]} {key[1]} {stamp}ms',fill=(25,25,25))
        sheet.save(REVIEW,quality=90)
        with CACHE.open('xb') as stream:np.savez_compressed(stream,features=features)
        document.update(state='prepared',feature_cache_sha256=sha(CACHE),visual_review={'path':REVIEW.relative_to(ROOT).as_posix(),'sha256':sha(REVIEW),'inspected':False},
            encoding_seconds=round(time.perf_counter()-began,3))
        atomic_json(REPORT,document);print(json.dumps({'prepared':True,'usable':int(np.isfinite(features).all(1).sum()),'total':97}),flush=True)
    except BaseException as error:
        document.update(state='failed',error={'type':type(error).__name__,'message':str(error)[:1200]});atomic_json(REPORT,document);raise


def train():
    d=json.loads(REPORT.read_text(encoding='utf-8'))
    if d['state']!='prepared' or not d['visual_review']['inspected'] or sha(REVIEW)!=d['visual_review']['sha256']:raise ValueError('必须先查看真实裁片核对图')
    if d['protocol']['script_sha256']!=sha(Path(__file__)) or sha(CACHE)!=d['feature_cache_sha256']:raise ValueError('固定协议或缓存变化')
    if DEST.exists():raise ValueError('原训练输出已存在，不覆盖')
    rows=cases();records=d['inputs']
    if [r['id'] for r in rows]!=[r['id'] for r in records]:raise ValueError('输入顺序变化')
    with np.load(CACHE,allow_pickle=False) as z:matrix=z['features']
    valid=np.isfinite(matrix).all(1);DEST.mkdir();folds=[];predictions=[];began=time.perf_counter()
    d.update(state='training',pid=os.getpid());atomic_json(REPORT,d)
    try:
        for fold in range(4):
            ti=[i for i,r in enumerate(rows) if r['condition_fold']!=fold and valid[i]];vi=[i for i,r in enumerate(rows) if r['condition_fold']==fold]
            assert_source_split([{**rows[i],'partition':'train'} for i in ti],[{**rows[i],'partition':'validation'} for i in vi])
            scaler=StandardScaler().fit(matrix[ti]);head=LogisticRegression(C=1.,max_iter=1000,random_state=20261103)
            weights=source_weights([{**rows[i],'action_id':rows[i]['source_label']} for i in ti]);head.fit(scaler.transform(matrix[ti]),[rows[i]['source_label'] for i in ti],sample_weight=weights)
            out=[]
            for i in vi:
                value={k:rows[i][k] for k in ('id','source_sha256','source_subject','source_label','condition_fold')}
                if valid[i]:
                    probabilities=head.predict_proba(scaler.transform(matrix[i:i+1]))[0];top=int(probabilities.argmax());score=float(probabilities[top]);value.update(top=str(head.classes_[top]),score=score,accepted=str(head.classes_[top]) if score>=.8 else None)
                else:value.update(top=None,score=None,accepted=None,reason=records[i]['reason'])
                out.append(value)
            path=DEST/f'fold-{fold}.joblib'
            with path.open('xb') as stream:joblib.dump({'scaler':scaler,'classifier':head,'runtime_enabled':False,'protocol':d['protocol']},stream,compress=3)
            restored=joblib.load(path);np.testing.assert_array_equal(head.predict_proba(scaler.transform(matrix[ti])),restored['classifier'].predict_proba(restored['scaler'].transform(matrix[ti])))
            folds.append({'fold':fold,'metrics':condition_metrics(out),'predictions':out,'fit_ids':[rows[i]['id'] for i in ti],
                'fit_subjects':sorted({rows[i]['source_subject'] for i in ti}),'evaluation_subjects':sorted({rows[i]['source_subject'] for i in vi}),
                'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'reload_exact':True,'iterations':head.n_iter_.tolist()})
            predictions.extend(out);d['fits']+=1;print(json.dumps({'fold':fold,'metrics':folds[-1]['metrics']}),flush=True)
        metrics=condition_metrics(predictions);d.update(state='completed',folds=folds,metrics=metrics,gate=release_gate(metrics,folds),training_seconds=round(time.perf_counter()-began,3));atomic_json(REPORT,d);print(json.dumps({'completed':True,'metrics':metrics,'gate':d['gate']}),flush=True)
    except BaseException as error:
        d.update(state='failed',error={'type':type(error).__name__,'message':str(error)[:1200]});atomic_json(REPORT,d);raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['prepare','train']);args=parser.parse_args()
    prepare() if args.phase=='prepare' else train()
