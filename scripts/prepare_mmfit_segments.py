"""把已画面复核的MM-Fit动作区间纳入训练；保留不匹配变式和休息负例。"""
import csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.research_extract import atomic_json

MAPPING={'squats':'bodyweight_squat','pushups':'standard_push_up','dumbbell_shoulder_press':'dumbbell_shoulder_press',
         'situps':'bent_knee_sit_up','tricep_extensions':'overhead_dumbbell_extension','bicep_curls':'standing_dumbbell_curl',
         'lateral_shoulder_raises':'dumbbell_lateral_raise','jumping_jacks':'jumping_jack'}


def run():
    directory=ROOT/'data/datasets/mmfit';path=ROOT/'data/datasets/development-movements/gym-segments.json'
    data=json.loads(path.read_text(encoding='utf-8'));data['segments']=[r for r in data['segments'] if r.get('segment_owner')!='mmfit']
    sources={r['id']:r for r in json.loads((path.parent/'sources.json').read_text(encoding='utf-8'))['sources']}
    for workout in ('w19','w16','w17'):
        source=sources['mmfit-'+workout];labels=list(csv.reader((directory/(workout+'_labels.csv')).read_text().splitlines()))
        def add(start,end,action,note,raw=None):
            data['segments'].append({'id':source['id']+f'-{start}-{end}','source_id':source['id'],'source_sha256':source['sha256'],
                                     'action_id':action,'start_ms':start,'end_ms':end,'partition':'train','group':'mmfit_all_workouts','crop':None,
                                     'segment_owner':'mmfit','review_method':note,'publisher_label':raw,'specific_error_ground_truth':False})
        previous=0;negatives=0
        for first,last,reps,raw in labels:
            start,end=round(int(first)/30*1000)+800,round(int(last)/30*1000)-800
            action=MAPPING.get(raw)
            if workout=='w19' and raw=='pushups':action=None
            if end-start>=3200:
                if action:add(start,end,action,'publisher interval + Codex complete-set middle-frame contact review; 0.8s edge trim',raw)
                elif negatives<4:
                    add(start,min(start+8000,end),'unknown','reviewed unsupported variation: knee push-ups, forward weighted lunge or dumbbell row; do not remap to existing standard/reverse/barbell class',raw);negatives+=1
            # 同一场次全属训练，取少量区间中部休息作负例，不随机把工作组标成休息。
            begin=round(int(first)/30*1000)
            if begin-previous>14000 and negatives<8:
                center=(previous+begin)//2;add(center-3000,center+3000,'unknown','publisher-labelled gap midpoint; action-absence weak negative; no posture-error label','rest_gap');negatives+=1
            previous=round(int(last)/30*1000)
    # 独立发布者的空手讲解负例；不把无动作输出计入正例识别正确率。
    for key,start,end in [('youtube-OOe_HrNnQWw',20000,35000),('youtube-Dbly77Jgbo8',10000,23000)]:
        source=sources[key]
        data['segments'].append({'id':key+f'-{start}-{end}-negative','source_id':key,'source_sha256':source['sha256'],
                                 'action_id':'unknown','start_ms':start,'end_ms':end,'partition':'validation','group':source['source_group'],'crop':None,
                                 'segment_owner':'mmfit','review_method':'Codex contact review: single trainer talking without moving weights','specific_error_ground_truth':False})
    atomic_json(path,data)
    print(json.dumps({'total_segments':len(data['segments']),'added':sum(r.get('segment_owner')=='mmfit' for r in data['segments'])}))


if __name__=='__main__':run()
