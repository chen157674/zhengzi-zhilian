"""上游算法和本地ONNX的真实数值复现，不运行安装/摄像头/GUI。"""
import ast,json,sys
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.pretrained_resources import repo_asset,onnx_asset,registry
from scripts.prepare_pretrained_correction import atomic_json,WORK


def function(path,name,namespace):
    tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    scope=dict(namespace);exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),scope)
    return scope[name]


def run():
    from PIL import Image
    repo=ROOT/registry()['repos']['rtmlib']['path']
    for item in registry()['repos']['rtmlib']['assets']:repo_asset('rtmlib',item['path'])
    sys.path.insert(0,str(repo))
    from rtmlib import RTMPose as Official
    from rtmlib.tools.pose_estimation.pre_processings import bbox_xyxy2cs,top_down_affine
    from fitness_app.rtmpose_input import RTMPose
    rgb=np.array(Image.open(ROOT/'data/experiments/open-source-correction/repos/mmpose/tests/data/coco/000000000785.jpg').convert('RGB'));h,w=rgb.shape[:2];reports=[]
    for kind in ('body','hand'):
        own=RTMPose(kind);path,record=onnx_asset('rtmpose-'+kind)
        original=Official(str(path),model_input_size=(own.width,own.height),backend='onnxruntime',device='cpu')
        for box in ([0,0,w,h],[w*.15,h*.1,w*.85,h*.9]):
            result=own.detect(rgb,box);xy,up_scores=original(rgb,[box])
            values=np.array([[p['x']*w,p['y']*h] for p in result['keypoints']]);error=float(np.max(np.abs(values-xy[0])))
            center,scale=bbox_xyxy2cs(np.array(box),padding=1.25)
            transformed,_=top_down_affine((own.width,own.height),scale,center,rgb)
            normalized=(transformed.astype(np.float32)-np.array([123.675,116.28,103.53],np.float32))/np.array([58.395,57.12,57.375],np.float32)
            outputs=own.session.run(None,{own.session.get_inputs()[0].name:np.ascontiguousarray(normalized.transpose(2,0,1)[None])})
            scores=np.minimum(outputs[0][0].max(-1),outputs[1][0].max(-1))
            score_error=float(np.max(np.abs(scores-np.array([p['score'] for p in result['keypoints']]))))
            if error>3. or score_error>.01:raise ValueError('真实ONNX数值对照超出三像素及0.01响应容差')
            reports.append({'kind':kind,'bbox':box,'model_sha256':record['sha256'],'coordinate_max_error_px':error,'coordinate_tolerance_px':3.,'score_tolerance':.01,'exact_equivalence':False,
                'minimum_axis_score_max_error':score_error,'rtmlib_mean_vs_project_min_difference':float(np.max(np.abs(scores-up_scores[0]))),
                'hand_on_body_image_numeric_parity_only':kind=='hand'})
    path,_=repo_asset('pose2sim-filtering','Pose2Sim/filtering.py')
    samples=np.array([1.,1.,1.1,.9,1.,15.,1.,1.1,1.,1.,1.]);filtered=function(path,'hampel_filter',{'np':np})(samples,7,2)
    if filtered[5]>2:raise ValueError('原异常点过滤复现失败')
    prefix='AI-Fitness-Trainer-Using-MediaPipe-Analyzing-Squats/'
    path,_=repo_asset('learnopencv-fitness',prefix+'process_frame.py');thresholds,_=repo_asset('learnopencv-fitness',prefix+'thresholds.py')
    cls=next(n for n in ast.parse(path.read_text(encoding='utf-8')).body if isinstance(n,ast.ClassDef) and n.name=='ProcessFrame')
    methods=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in ('_get_state','_update_state_sequence')]
    scope={};safe=ast.ClassDef(name='OriginalStates',bases=[],keywords=[],body=methods,decorator_list=[])
    exec(compile(ast.fix_missing_locations(ast.Module(body=[safe],type_ignores=[])),str(path),'exec'),scope)
    actor=scope['OriginalStates']();actor.thresholds=function(thresholds,'get_thresholds_beginner',{})();actor.state_tracker={'state_seq':[]}
    states=[actor._get_state(v) for v in (10,45,80,45,10)]
    for state in states:actor._update_state_sequence(state)
    if states!=['s1','s2','s3','s2','s1'] or actor.state_tracker['state_seq']!=['s2','s3','s2']:raise ValueError('原阶段状态机复现失败')
    report={'schema_version':1,'scope':'original_algorithms_and_real_onnx_not_independent_error_accuracy',
        'rtmlib':reports,'rtmlib_score_contract':'新版2D均值分数与本项目原MMPose最小轴分数不同，保留原严格可见性口径',
        'pose2sim_hampel':{'input':samples.tolist(),'output':filtered.tolist(),'GUI_or_OpenSim_imported':False},
        'learnopencv':{'states':states,'sequence':actor.state_tracker['state_seq'],'unsafe_generalized_labels_not_adopted':True},
        'private_media_uploaded':False,'paid_api_calls':0,'models_downloaded':0}
    atomic_json(WORK/'movement-guidance-reproduction.json',report)
    print(json.dumps({'real_onnx_comparisons':len(reports),'max_coordinate_error_px':max(r['coordinate_max_error_px'] for r in reports),'hampel_outlier':float(filtered[5]),'states':states},ensure_ascii=False),flush=True)

if __name__=='__main__':run()