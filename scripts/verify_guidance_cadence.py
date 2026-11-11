"""真实原生姿态缓存的稀疏采样对照；不修改点、不制造具体错误真值。"""
import copy,gzip,hashlib,inspect,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app import movement_guidance as guidance
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.posture_engine import resolve_view
from fitness_app.media import probe_video
from fitness_app.research_extract import atomic_json


def original_observer():
    source=inspect.getsource(guidance)
    start=source.index('        gap=stamp-self.last_stamp if self.last_stamp is not None else None')
    end=source.index('        self.last_stamp=stamp;self.basis=basis',start)
    source=source[:start]+"        if self.last_stamp is not None and (stamp<=self.last_stamp or stamp-self.last_stamp>250 or self.basis!=basis):self.reset()\n"+source[end:]
    start=source.index("                    intervals=[b['stamp']-a['stamp'] for a,b in zip(rows,rows[1:])]")
    end=source.index('                        excursion=',start)
    source=source[:start]+"                    if len(rows)>=8 and stamp-rows[0]['stamp']>=1200:\n"+source[end:]
    source=source.replace(",\n                            'observed_frames':len(rows),'observed_duration_ms':stamp-rows[0]['stamp'],\n                            'maximum_observation_gap_ms':max(intervals) if intervals else None",'')
    start=source.index('                    intervals=[b[0]-a[0] for a,b in zip(rows,list(rows)[1:])]')
    end=source.index('                    bad=degrees>30',start)
    source=source[:start]+"                    while rows and stamp-rows[0][0]>1000:rows.popleft()\n                    ready=len(rows)>=5 and stamp-rows[0][0]>=700;degrees=float(median(v for _,v in rows))\n"+source[end:]
    source=source.replace(",\n                                       'observed_frames':len(rows),'maximum_observation_gap_ms':max(intervals) if intervals else None",'')
    namespace={'__name__':'fitness_app._previous_guidance','__package__':'fitness_app'}
    exec(compile(source,'previous_movement_guidance_reference','exec'),namespace)
    return namespace['MovementGuidance'],hashlib.sha256(source.encode()).hexdigest()


def run():
    destination=ROOT/'data/verification/round3/movement-guidance-cadence-review.json'
    if destination.exists():raise ValueError('已有同批采样对照，不重复覆盖')
    original,old_sha=original_observer();manifest=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    streams=[]
    for number in ('1688','1689'):
        path=ROOT/f'data/experiments/open-source-correction/user-{number}-native-inputs.json.gz'
        with gzip.open(path,'rt',encoding='utf-8') as source:cache=json.load(source)
        item=manifest['demos']['user-'+number]
        if cache['video_sha256']!=item['video']['sha256']:raise ValueError('姿态缓存身份变化')
        streams.append((item,cache['rows'],path))
    old_path=ROOT/'data/verification/round3/movement-guidance-review.json'
    cached=json.loads(old_path.read_text(encoding='utf-8'))
    for number in ('1693','1575'):
        video=next(v for v in cached['videos'] if v['number']==number);item=manifest['demos']['user-'+number]
        if video['source_sha256']!=item['video']['sha256']:raise ValueError('旧原生姿态缓存与当前原片不符')
        info=probe_video(ROOT/item['video']['path']);rows=[]
        for frame in video['frames']:
            data={'status':'observed','timestamp_ms':frame['t'],'body':frame['body'],'body_features':frame['body_features'],
                'hands':frame['hands'],'source_landmarks':frame['original_landmarks']}
            result={'poses':[frame['original_landmarks']] if frame['base_people']==1 else [[]]*frame['base_people'],'pretrained':data}
            rows.append({'t':frame['t'],'width':info['width'],'height':info['height'],'pose_result':result,'identity_accepted':frame['identity_accepted']})
        streams.append((item,rows,old_path))
    reviews=[]
    for item,rows,path in streams:
        if hashlib.sha256((ROOT/item['video']['path']).read_bytes()).hexdigest()!=item['video']['sha256']:raise ValueError('当前视频摘要变化')
        for cadence in (100,200,280,333,400,500):
            selected=[];last=None
            for row in rows:
                if last is not None and row['t']-last<cadence:continue
                selected.append(row);last=row['t']
            comparisons={}
            for label,kind in (('previous',original),('adapted',guidance.MovementGuidance)):
                observer=kind(item['action_id'],'cadence-review');counts=Counter();issues=[]
                for index,row in enumerate(selected):
                    result=copy.deepcopy(row['pose_result']);q=analysis_framing(item['action_id'],result)
                    f={'captured_ms':row['t'],'frame_seq':index,'width':row['width'],'height':row['height'],
                        'framing':q,'identity_accepted':row.get('identity_accepted',True),'pretrained':result.get('pretrained')}
                    f['posture_view']=resolve_view(f,'auto')[0]
                    items,findings=observer.update(f,{})
                    for value in items:counts[value['code']+':'+value['status']]+=1
                    issues.extend({k:v for k,v in value.items() if k in ('code','side','start_ms','end_ms','evidence')} for value in findings if value['status']=='issue')
                comparisons[label]={'counts':dict(counts),'issues':issues}
            reviews.append({'source_id':item['id'],'source_sha256':item['video']['sha256'],
                'numeric_cache_path':path.relative_to(ROOT).as_posix(),'numeric_cache_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'requested_cadence_ms':cadence,'actual_observations':len(selected),'conditions':comparisons})
            print(json.dumps({'source':item['id'],'cadence':cadence,'issue_counts':{k:len(v['issues']) for k,v in comparisons.items()}}),flush=True)
    atomic_json(destination,{'state':'completed','scope':'same_real_pose_numeric_cache_sparse_sampling_not_independent_error_truth',
        'previous_reference_sha256':old_sha,'current_implementation_sha256':hashlib.sha256(Path(guidance.__file__).read_bytes()).hexdigest(),
        'new_pose_inferences':0,'paid_api_calls':0,'private_media_uploaded':False,'reviews':reviews})


if __name__=='__main__':run()
