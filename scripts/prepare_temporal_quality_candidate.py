"""取得一个可核查的已训练时序纠错候选；固定提交，只保存所需源码和单项权重，不启用生产。"""
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json
from scripts.prepare_pretrained_correction import update

REPO='evlo-malik/exercise-advisor'
COMMIT='522e939f5eac9622430096e1911e91e7132945e6'
SOURCE=project_path('data/experiments/open-source-correction/repos/exercise-advisor')
MODELS=project_path('data/models/open-source-correction/exercise-advisor')
WEIGHT='checkpoints/OHP_error_knees.pt'
FILES=('LICENSE','README.md','docs/dataset.md','docs/results.md','checkpoints/README.md',
       'src/exercise_advisor/config.py','src/exercise_advisor/features/features.py',
       'src/exercise_advisor/models/tcn.py','src/exercise_advisor/models/checkpoint.py',
       'src/exercise_advisor/pose/mediapipe_extractor.py','src/exercise_advisor/pose/landmarks.py',WEIGHT)


def fetch(url,maximum):
    request=urllib.request.Request(url,headers={'User-Agent':'fitness-assistant-local-research'})
    with urllib.request.urlopen(request,timeout=30) as response:
        content=response.read(maximum+1)
    if len(content)>maximum:raise ValueError('资源超过本次明确的大小边界')
    return content


def run():
    tree=json.loads(fetch('https://api.github.com/repos/'+REPO+'/git/trees/'+COMMIT+'?recursive=1',1024*1024))
    entries={item['path']:item for item in tree['tree'] if item['type']=='blob'}
    if any(path not in entries for path in FILES):raise ValueError('固定提交缺少所需资源')
    if entries[WEIGHT]['size']!=2635020:raise ValueError('单项权重大小不符')
    checked=[]
    for relative in FILES:
        entry=entries[relative]
        if entry['size']>4*1024*1024:raise ValueError('资源异常大，停止取得')
        target=project_path(MODELS/'OHP_error_knees.pt' if relative==WEIGHT else SOURCE/relative)
        content=target.read_bytes() if target.exists() else fetch('https://raw.githubusercontent.com/'+REPO+'/'+COMMIT+'/'+relative,entry['size'])
        git_sha=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
        if len(content)!=entry['size'] or git_sha!=entry['sha']:raise ValueError('资源与固定Git提交不一致：'+relative)
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(content)
        checked.append({'source_path':relative,'path':target.relative_to(ROOT).as_posix(),
            'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),'git_blob_sha1':git_sha})
        print(json.dumps({'checked':relative,'bytes':len(content)}),flush=True)
    record={'state':'downloaded_not_evaluated','source_repo':REPO,'source_commit':COMMIT,'license':'MIT',
        'license_path':str((SOURCE/'LICENSE').relative_to(ROOT).as_posix()),'scope':'strict_barbell_overhead_press_knee_bend_only',
        'files':checked,'upstream_threshold':.55,
        'upstream_reported_test':{'precision':.713,'recall':.602,'f1':.653,'roc_auc':.802,
            'positive_reps':128,'negative_reps':211,'source':'docs/results.md','reproduced_in_this_project':False},
        'production_enabled':False,'new_training_performed':False,
        'selection_reason':'只核查有较高来源精确率且符合严格推举语义的单项；其余类别不直接启用',
        'remaining_gates':['逐行审查输入与网络','安全加载及数值对照','与计数／阶段衔接','独立误报和漏报验证']}
    atomic_json(MODELS/'manifest.json',record)
    update('temporal_quality_candidates','exercise-advisor-ohp-knees',record)
    print(json.dumps({'candidate':'OHP_error_knees','state':record['state'],'production_enabled':False}),flush=True)


if __name__=='__main__':run()
