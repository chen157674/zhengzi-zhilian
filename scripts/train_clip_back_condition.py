"""冻结CLIP与原灰度躯干的四折来源条件对照；不读test或启用生产。"""
import hashlib,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
import numpy as np
import onnxruntime as ort
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import joblib
from fitness_app.research_extract import atomic_json
from fitness_app.visual_recognition import DIRECTORY,normalize
from scripts.train_visual_posture import cases,sha
from scripts.train_back_image_adapter import inputs
from scripts.train_deadlift_condition import condition_metrics,release_gate
from scripts.partial_action_training import source_weights,assert_source_split
REPORT=ROOT/'data/verification/round3/deadlift-clip-condition-training.json'
CACHE=ROOT/'data/experiments/features/deadlift-clip-torso.npz'
DEST=ROOT/'data/models/open-source-correction/deadlift-clip-head'
CONFIG={'seed':20261103,'C':1.,'max_iter':1000,'minimum_score':.8,
    'input':'original_frozen_eight_gray_torso_patches','mirror':'average_l2_original_and_flip',
    'pool':['mean','std_ddof_0'],'backbone_frozen':True,'production_enabled':False}


def run():
    if REPORT.exists():raise ValueError('已有固定CLIP实验，不重复启动或覆盖')
    if CACHE.exists() or DEST.exists():raise ValueError('候选输出已存在，先核身份，不覆盖')
    rows=cases();patches,records=inputs(rows)
    old=json.loads((ROOT/'data/verification/round3/deadlift-image-adapter-training.json').read_text(encoding='utf-8'))
    if records!=old['inputs']:raise ValueError('实际灰度像素与原输入摘要不相同')
    manifest=json.loads((DIRECTORY/'manifest.json').read_text(encoding='utf-8'))
    for name in ('onnx/vision_model_quantized.onnx','preprocessor_config.json'):
        if sha(DIRECTORY/name)!=manifest['files'][name]['sha256']:raise ValueError('CLIP资源身份变化')
    processor=json.loads((DIRECTORY/'preprocessor_config.json').read_text(encoding='utf-8'))
    protocol={'config':CONFIG,'source_index_sha256':sha(ROOT/'data/datasets/index.csv'),
        'backbone_revision':manifest['revision'],'backbone_sha256':manifest['files']['onnx/vision_model_quantized.onnx']['sha256'],
        'original_input_report_sha256':sha(ROOT/'data/verification/round3/deadlift-image-adapter-training.json'),
        'script_sha256':sha(Path(__file__)),'inputs':records}
    doc={'state':'encoding','pid':os.getpid(),'protocol':protocol,'private_media_uploaded':False,'paid_api_calls':0,
        'test_media_or_features_read':False,'new_pose_inferences':0,'new_encoded_clips':0,'fits':0,
        'production_enabled':False,'scope':'whole_clip_publisher_GB_PB_subject_separated_not_frame_error_truth'}
    atomic_json(REPORT,doc);began=time.perf_counter()
    try:
        options=ort.SessionOptions();options.intra_op_num_threads=2;options.inter_op_num_threads=1
        session=ort.InferenceSession(str(DIRECTORY/'onnx/vision_model_quantized.onnx'),options,providers=['CPUExecutionProvider'])
        matrix=np.full((len(rows),1024),np.nan,np.float32)
        for index,patch in enumerate(patches):
            if patch is None:continue
            pixels=patch.numpy();pixel_sha=hashlib.sha256(pixels.tobytes()).hexdigest()
            if pixel_sha!=records[index]['gray_patch_sha256']:raise ValueError('灰度输入像素变化')
            encoded=[]
            for frame in pixels:
                variants=[]
                for image in (frame,frame[:,::-1]):
                    value=np.repeat(image[:,:,None],3,axis=2).astype(np.float32)*np.float32(processor['rescale_factor'])
                    value=(value-np.asarray(processor['image_mean'],np.float32))/np.asarray(processor['image_std'],np.float32)
                    tensor=np.ascontiguousarray(value.transpose(2,0,1)[None])
                    output=session.run(['image_embeds'],{'pixel_values':tensor})[0]
                    if output.shape!=(1,512) or not np.isfinite(output).all():raise ValueError('CLIP输出结构无效')
                    variants.append(normalize(output)[0])
                encoded.append(np.mean(variants,axis=0))
            features=np.asarray(encoded,np.float32)
            matrix[index]=np.concatenate((features.mean(0),features.std(0)))
            doc['new_encoded_clips']+=1
            if doc['new_encoded_clips']%10==0:
                atomic_json(REPORT,doc);print(json.dumps({'encoded':doc['new_encoded_clips'],'total':94}),flush=True)
        valid=np.isfinite(matrix).all(1)
        if valid.sum()!=94:raise ValueError('不能删去原三段拒判或可用片段')
        with CACHE.open('xb') as stream:np.savez_compressed(stream,features=matrix,protocol_sha256=np.array(hashlib.sha256(json.dumps(protocol,sort_keys=True).encode()).hexdigest()))
        doc.update(state='training',feature_cache_sha256=sha(CACHE),encoding_seconds=round(time.perf_counter()-began,3));atomic_json(REPORT,doc)
        DEST.mkdir();predictions=[];folds=[]
        for fold in range(4):
            train=[i for i,r in enumerate(rows) if r['condition_fold']!=fold and valid[i]]
            evaluation=[i for i,r in enumerate(rows) if r['condition_fold']==fold]
            assert_source_split([{**rows[i],'partition':'train'} for i in train],[{**rows[i],'partition':'validation'} for i in evaluation])
            scaler=StandardScaler().fit(matrix[train]);classifier=LogisticRegression(C=CONFIG['C'],max_iter=CONFIG['max_iter'],random_state=CONFIG['seed'])
            weights=source_weights([{**rows[i],'action_id':rows[i]['source_label']} for i in train])
            classifier.fit(scaler.transform(matrix[train]),[rows[i]['source_label'] for i in train],sample_weight=weights)
            out=[]
            for i in evaluation:
                value={k:rows[i][k] for k in ('id','source_sha256','source_subject','source_label','condition_fold')}
                if valid[i]:
                    scores=classifier.predict_proba(scaler.transform(matrix[i:i+1]))[0];top=int(np.argmax(scores));score=float(scores[top])
                    value.update(top=str(classifier.classes_[top]),score=score,accepted=str(classifier.classes_[top]) if score>=CONFIG['minimum_score'] else None)
                else:value.update(top=None,score=None,accepted=None,reason=records[i]['reason'])
                out.append(value)
            destination=DEST/f'fold-{fold}.joblib'
            with destination.open('xb') as stream:joblib.dump({'scaler':scaler,'classifier':classifier,'runtime_enabled':False,'protocol':protocol},stream,compress=3)
            loaded=joblib.load(destination)
            np.testing.assert_array_equal(classifier.predict_proba(scaler.transform(matrix[train])),loaded['classifier'].predict_proba(loaded['scaler'].transform(matrix[train])))
            result={'fold':fold,'metrics':condition_metrics(out),'predictions':out,'fit_ids':[rows[i]['id'] for i in train],
                'fit_subjects':sorted({rows[i]['source_subject'] for i in train}),'evaluation_subjects':sorted({rows[i]['source_subject'] for i in evaluation}),
                'iterations':classifier.n_iter_.tolist(),'path':destination.relative_to(ROOT).as_posix(),'sha256':sha(destination),'reload_exact':True}
            folds.append(result);predictions.extend(out);doc['fits']+=1
            print(json.dumps({'fold':fold,'metrics':result['metrics']}),flush=True)
        metrics=condition_metrics(predictions);gate=release_gate(metrics,folds)
        doc.update(state='completed',metrics=metrics,folds=folds,gate=gate,total_seconds=round(time.perf_counter()-began,3))
        atomic_json(REPORT,doc);print(json.dumps({'completed':True,'metrics':metrics,'gate':gate}),flush=True)
    except BaseException as error:
        doc.update(state='failed',error={'type':type(error).__name__,'message':str(error)[:1500]});atomic_json(REPORT,doc);raise


if __name__=='__main__':run()
