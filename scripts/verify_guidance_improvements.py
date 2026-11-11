"""冻结开源轨迹与真实姿态缓存的前后对照；粗标签不等于具体错误真值。"""
import argparse,copy,gzip,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.movement_guidance import MovementGuidance
from fitness_app.posture_engine import resolve_view
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.research_extract import atomic_json
from scripts.evaluate_pose_trainer_quality import SOURCE_ROOT,EXPECTED_SOURCE,source_bundle,frames_from_sequence,sha
OUTPUT=ROOT/'data/verification/round3/movement-guidance-improvements.json'


def streams():
    actual={'license_sha256':sha(SOURCE_ROOT/'LICENSE'),'readme_sha256':sha(SOURCE_ROOT/'README.md'),
        'pose_sha256':sha(SOURCE_ROOT/'pose.py'),'utils_sha256':sha(SOURCE_ROOT/'utils.py'),**source_bundle()}
    if actual!=EXPECTED_SOURCE:raise ValueError('冻结源码、许可或112条轨迹摘要改变')
    for folder,action in [('bicep','standing_dumbbell_curl'),('shoulderpress','dumbbell_shoulder_press'),('frontraise','dumbbell_front_raise')]:
        for path in sorted((SOURCE_ROOT/'poses_compressed'/folder).glob('*.npy')):
            frames=frames_from_sequence(path)
            for frame in frames:frame['framing']['usable']=True
            yield {'id':path.relative_to(SOURCE_ROOT).as_posix(),'action':action,'sha256':sha(path),
                'scope':'public_coarse_label_unknown_fps','label':'good' if 'good' in path.name else 'bad'},frames,(33,200,400)
    assets=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    for number in ('1688','1689'):
        path=ROOT/f'data/experiments/open-source-correction/user-{number}-native-inputs.json.gz'
        with gzip.open(path,'rt',encoding='utf-8') as source:cache=json.load(source)
        asset=assets['demos']['user-'+number]
        if cache['video_sha256']!=asset['video']['sha256'] or sha(ROOT/asset['video']['path'])!=cache['video_sha256']:raise ValueError('原片与缓存身份改变')
        frames=[]
        for row in cache['rows']:
            result=copy.deepcopy(row['pose_result'])
            frame={'captured_ms':row['t'],'frame_seq':len(frames),'width':row['width'],'height':row['height'],
                'framing':analysis_framing(asset['action_id'],result),'identity_accepted':row.get('identity_accepted',True),
                'pretrained':result.get('pretrained')}
            frame['posture_view']=resolve_view(frame,'auto')[0];frames.append(frame)
        yield {'id':'user-'+number,'action':asset['action_id'],'sha256':sha(path),'scope':'real_pose_cache'},frames,(100,200,280,333,400,500)
    path=ROOT/'data/verification/round3/movement-guidance-review.json'
    cache=json.loads(path.read_text(encoding='utf-8'))
    for number in ('1693','1575'):
        asset=assets['demos']['user-'+number];video=next(v for v in cache['videos'] if v['number']==number)
        if sha(ROOT/asset['video']['path'])!=video['source_sha256']:raise ValueError('原片身份改变')
        frames=[]
        for row in video['frames']:
            frame={'captured_ms':row['t'],'frame_seq':len(frames),'width':asset['width'],'height':asset['height'],
                'identity_accepted':row['identity_accepted'],'framing':{'usable':row['identity_accepted'],
                'person_count':row['base_people'],'landmarks':row['original_landmarks']},
                'pretrained':{'status':'observed' if row.get('body') else 'unassessable','timestamp_ms':row['t'],
                'body':row.get('body'),'hands':row.get('hands',[])}}
            frame['posture_view']=resolve_view(frame,'auto')[0];frames.append(frame)
        yield {'id':'user-'+number,'action':asset['action_id'],'sha256':sha(path),'scope':'real_pose_cache'},frames,(100,200,280,333,400,500)


def evaluate():
    reviews=[]
    for identity,frames,cadences in streams():
        for cadence in cadences:
            observer=MovementGuidance(identity['action'],'improvements-review');counts=Counter();issues=[];last=None
            for frame in frames:
                stamp=frame['captured_ms']
                if last is not None and stamp-last<cadence:continue
                last=stamp;items,findings=observer.update(frame,{})
                counts.update(i['code']+':'+i['status'] for i in items)
                issues.extend({k:i[k] for k in ('code','side','start_ms','end_ms','evidence') if k in i} for i in findings if i['status']=='issue')
            reviews.append({**identity,'cadence_ms':cadence,'counts':dict(counts),'issues':issues})
    return {'implementation_sha256':sha(ROOT/'fitness_app/movement_guidance.py'),'reviews':reviews}


def run(stage):
    if stage=='baseline':
        if OUTPUT.exists():raise ValueError('已有基线，不覆盖')
        report={'scope':'development_regression_not_independent_specific_error_truth','source_registry_missing':not (ROOT/'third_party/sources.json').exists(),
            'source_verified_by_frozen_byte_hashes':True,'new_pose_inferences':0,'paid_api_calls':0,'private_media_uploaded':False,'baseline':evaluate()}
    else:
        report=json.loads(OUTPUT.read_text(encoding='utf-8'));current=evaluate();before=report['baseline']['reviews']
        if [(r['id'],r['cadence_ms'],r['sha256']) for r in before]!=[(r['id'],r['cadence_ms'],r['sha256']) for r in current['reviews']]:raise ValueError('前后输入不一致')
        report['current']=current
    atomic_json(OUTPUT,report)
    result=report['baseline' if stage=='baseline' else 'current']
    for scope in sorted({r['scope'] for r in result['reviews']}):
        rows=[r for r in result['reviews'] if r['scope']==scope]
        print(json.dumps({'stage':stage,'scope':scope,'streams':len(rows),'streams_with_issues':sum(bool(r['issues']) for r in rows),
            'issue_codes':dict(Counter(i['code'] for r in rows for i in r['issues']))}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['baseline','current']);run(parser.parse_args().stage)
