"""同源八图2/4线程交错计时与完整输出对照，保留所有身体／手部步骤。"""
import copy,hashlib,json,os,sys,time
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
os.environ['FITNESS_POSE_DEVICE']='cpu'
import numpy as np
from PIL import Image
from fitness_app.pretrained_input import PretrainedFrameModel
from fitness_app.rtmpose_input import COCO_TO_MEDIAPIPE,cpu_thread_count
from fitness_app.research_extract import atomic_json


def flatten(data):
    output=[]
    def add(p):output.extend([p['x'],p['y'],p['score'],float(p['coordinate_valid'])])
    for p in data['body']['keypoints']:add(p)
    for h in sorted(data['hands'],key=lambda h:h['body_wrist_index']):
        output.append(h['body_wrist_index'])
        for p in h['keypoints']:add(p)
    return np.asarray(output)


def main():
    source=ROOT/'data/verification/round3/pretrained-input-review.json';doc=json.loads(source.read_text(encoding='utf-8'));inputs=[]
    for row in doc['rows']:
        path=ROOT/row['source_image_path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['source_image_sha256']:raise ValueError('原图摘要改变')
        rgb=np.asarray(Image.open(path).convert('RGB'));points=[{'x':.5,'y':.5,'z':0.,'visibility':0.,'presence':0.} for _ in range(33)]
        for k,v in COCO_TO_MEDIAPIPE.items():
            p=row['body']['keypoints'][k];points[v].update(x=p['x'],y=p['y'],visibility=p['score'],presence=p['score'])
        inputs.append((row['id'],rgb,{'poses':[points]}))
    models={};totals=defaultdict(list);outputs={};rows=[]
    try:
        for threads in (2,4):
            os.environ['FITNESS_POSE_CPU_THREADS']=str(threads);models[threads]=PretrainedFrameModel(device='cpu')
        for repeat in range(3):
            for index,(identity,rgb,pose) in enumerate(inputs):
                for threads in ((2,4) if (index+repeat)%2==0 else (4,2)):
                    model=models[threads];model.axis.reset();stamp=(repeat*len(inputs)+index)*200
                    began=time.perf_counter();value=model.observe(rgb,copy.deepcopy(pose),stamp);elapsed=time.perf_counter()-began
                    current=flatten(value);outputs[(repeat,index,threads)]=current
                    totals[threads].append(elapsed);rows.append({'repeat':repeat,'id':identity,'threads':threads,'elapsed_seconds':elapsed,
                        'hands':len(value['hands']),'recovered':value['hand_recovery']['recovered']})
        maximum=0.
        for repeat in range(3):
            for index in range(len(inputs)):
                a,b=[outputs[(repeat,index,t)] for t in (2,4)]
                if a.shape!=b.shape:raise ValueError('线程改动导致检出结构变化')
                maximum=max(maximum,float(np.max(abs(a-b))))
                np.testing.assert_allclose(a,b,rtol=0,atol=1e-5)
        report={'scope':'same_eight_development_images_interleaved_cpu_sessions_not_independent_accuracy',
            'images':len(inputs),'repetitions':3,'rows':rows,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'timings':{str(t):{'total_seconds':sum(v),'median_frame_ms':float(np.median(v)*1000)} for t,v in totals.items()},
            'maximum_keypoint_or_response_difference':maximum,'all_output_shapes_equal':True,'sample_rate_changed':False,
            'thresholds_changed':False,'private_media_uploaded':False,'paid_api_calls':0,
            'implementation_sha256':hashlib.sha256((ROOT/'fitness_app/rtmpose_input.py').read_bytes()).hexdigest()}
        atomic_json(ROOT/'data/verification/round3/pose-thread-performance.json',report)
        print(json.dumps({k:report[k] for k in ('timings','maximum_keypoint_or_response_difference','all_output_shapes_equal')},ensure_ascii=False),flush=True)
    finally:
        for model in models.values():model.close()

if __name__=='__main__':main()
