"""对下载的原模型和原数据做本地运行复现，不把训练集成绩当独立验收。"""
import argparse
import csv
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.pretrained_resources import registry,repo_asset,checked
from scripts.prepare_pretrained_correction import WORK,update,atomic_json,sha

REPORT=WORK/'reproduction.json'


def psleon():
    import numpy as np
    from fitness_app.exercise_feedback import ExerciseFeedbackModel,ACTIONS
    results=[]
    for action,name in ACTIONS.items():
        path,resource=repo_asset('exercise-pose-feedback',f'Afterprocessing/{name}_coords.csv')
        with path.open(encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream);columns=[name for name in reader.fieldnames if name!='class'];rows=list(reader)
        features=np.asarray([[float(row[key]) for key in columns] for row in rows]);labels=np.array([row['class'] for row in rows])
        model=ExerciseFeedbackModel(action);start=time.perf_counter();predicted,probabilities=model.predict_array(features)
        results.append({'action_id':action,'samples':len(rows),'feature_columns':len(columns),'trees':len(model.trees),
            'classes':model.classes.tolist(),'csv_path':path.relative_to(ROOT).as_posix(),'csv_sha256':resource['sha256'],
            'model_sha256':model.resource['sha256'],'matched_labels':int((predicted==labels).sum()),
            'label_agreement':float((predicted==labels).mean()),'elapsed_seconds':round(time.perf_counter()-start,3),
            'probability_sum_max_error':float(abs(probabilities.sum(1)-1).max())})
    return {'state':'passed','scope':'upstream_csv_reexecution_not_independent_accuracy','models':results,
        'machine_chest_press_supported':False,'upstream_license':'not_declared_local_research_only'}


def sitrex():
    import numpy as np
    import torch
    from threadpoolctl import threadpool_limits
    from fitness_app.reference_similarity import ReferenceSimilarity
    torch.set_num_threads(2);model=ReferenceSimilarity();dataset=registry()['datasets']['sitrex']
    sequences=[]
    for item in dataset['files']:
        if item['upstream_path'].endswith('exercise_angles.json'):continue
        data=json.loads(checked(item['path'],item).read_text(encoding='utf-8'))
        values=model.angles(data['frames'],normalized=True)
        sequences.append({'path':item['path'],'sha256':item['sha256'],'action':data['label'],'values':values})
    probe=sequences[0]['values'][None,:min(12,len(sequences[0]['values'])),:]
    errors={}
    with threadpool_limits(limits=2):
        for name,network in (('similarity',model.similarity),('usefulness',model.usefulness)):
            error=float(np.max(np.abs(network.encode(probe)-network.encode_numpy(probe))))
            errors[name]=error
            if error>3e-3:raise ValueError('Torch与NumPy数值实现不一致：'+str(error))
        comparisons=[]
        for index in range(0,len(sequences),2):
            left,right=sequences[index:index+2]
            if left['action']!=right['action']:raise ValueError('原数据动作分组不符')
            start=time.perf_counter()
            result=model.compare(left['values'],right['values'],left['action'],left['action'])
            comparisons.append({'reference_path':left['path'],'reference_sha256':left['sha256'],
                'observed_path':right['path'],'observed_sha256':right['sha256'],
                'reference_frames':len(left['values']),'observed_frames':len(right['values']),
                'elapsed_seconds':round(time.perf_counter()-start,3),'result':result})
    return {'state':'passed','scope':'original_pose_subset_frozen_weight_inference_not_paper_accuracy',
        'original_dataset':'abdellah213/sitrex-dataset','dataset_version':2,'dataset_license':'CC0: Public Domain',
        'angle_names':model.angle_names,'angle_type':'sagittal','numeric_encoder_max_abs_errors':errors,
        'usefulness_weight_tensors_consumed':model.usefulness.weight_tensors,
        'similarity_weight_tensors_consumed':model.similarity.weight_tensors,
        'published_weights_contain_positional_embedding':False,'comparisons':comparisons,
        'upstream_notebook_weight_paths_corrected':True,'tensorflow_installed_or_required':False}


def main(stage):
    document=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else {'schema_version':1,'no_private_media_uploaded':True}
    for name,run in {'psleon':psleon,'sitrex':sitrex}.items():
        if stage not in (name,'all'):continue
        try:
            result=run();document[name]=result;atomic_json(REPORT,document)
            update('reproductions',name,{'state':'passed','report_path':REPORT.relative_to(ROOT).as_posix(),
                'script_sha256':sha(Path(__file__)),'production_enabled':False})
            print(json.dumps({'stage':name,'state':'passed','details':result.get('models',{}),'numerical_errors':result.get('numeric_encoder_max_abs_errors')},ensure_ascii=False),flush=True)
        except Exception as error:
            document[name]={'state':'failed','error_type':type(error).__name__,'error':str(error)};atomic_json(REPORT,document)
            raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['psleon','sitrex','all'],default='all',nargs='?');args=parser.parse_args()
    main(args.stage)
