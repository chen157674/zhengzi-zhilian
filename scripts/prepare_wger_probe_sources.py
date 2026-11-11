"""按逐视频CC BY-SA许可准备新来源观察集；整位作者仅诊断，不进入训练。"""
import hashlib,json,sys,shutil
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
from fitness_app.research_extract import atomic_json
import av

DIRECTORY=ROOT/'data/datasets/development-movements'
SELECTED={1:'seated_cable_row',7:'bent_over_dumbbell_reverse_fly',8:'bent_over_dumbbell_reverse_fly',17:'dumbbell_bench_press',18:'dumbbell_bench_press',48:'standing_dumbbell_curl',51:'hammer_curl',78:'cable_face_pull',79:'cable_face_pull'}


def metadata(path):
    with urlopen(Request('https://wger.de/api/v2/'+path,headers={'User-Agent':'fitness-assistant-research/1.0','Accept':'application/json'}),timeout=40) as r:return json.load(r)


def main():
    inventory=json.loads((DIRECTORY/'wger-video-inventory.json').read_text(encoding='utf-8'));licenses={r['id']:r for r in metadata('license/?limit=30')['results']}
    manifest=DIRECTORY/'sources.json';data=json.loads(manifest.read_text(encoding='utf-8'));by={s['id']:s for s in data['sources']}
    if shutil.disk_usage(DIRECTORY).free<512*1024*1024:raise ValueError('本次素材剩余空间不足')
    for vid,action in SELECTED.items():
        sid='wger-video-'+str(vid)
        if sid in by:
            old=by[sid];path=project_path(old['path'])
            if hashlib.sha256(path.read_bytes()).hexdigest()!=old['sha256']:raise ValueError('已登记wger原片身份改变')
            print(json.dumps({'source':sid,'reused':True}),flush=True);continue
        item=metadata('video/'+str(vid)+'/');license=licenses[item['license']];url=item['video'];parsed=urlparse(url)
        if license['short_name']!='CC-BY-SA 4' or parsed.scheme!='https' or parsed.hostname!='wger.de' or not parsed.path.startswith('/media/exercise-video/'):raise ValueError('许可/公开素材域名与固定观察计划不一致')
        if not item['license_author'] or item['size']>64*1024*1024:raise ValueError('作者未署名或素材超限')
        extension=Path(parsed.path).suffix.lower();folder=project_path(DIRECTORY/'wger-probe');folder.mkdir(exist_ok=True);target=folder/(sid+extension)
        if target.exists():raise ValueError('未登记的目标已存在，不覆盖')
        temporary=target.with_suffix('.part'+extension);owned=False
        try:
            with temporary.open('xb') as output:
                owned=True
                with urlopen(Request(url,headers={'User-Agent':'fitness-assistant-research/1.0'}),timeout=60) as response:
                    amount=0
                    while block:=response.read(1024*1024):
                        amount+=len(block)
                        if amount>64*1024*1024:raise ValueError('素材流超过上限')
                        output.write(block)
            if temporary.stat().st_size!=item['size']:raise ValueError('素材体积不匹配，未接纳不完整下载')
            with av.open(str(temporary)) as video:
                stream=video.streams.video[0];duration=round(float(video.duration)/1000);info={'duration_ms':duration,'width':stream.width,'height':stream.height,'codec':stream.codec_context.name}
            temporary.replace(target)
        finally:
            if owned and temporary.exists():temporary.unlink()
        names=inventory['exercises'][str(item['exercise'])]['names']
        row={'id':sid,'path':target.relative_to(ROOT).as_posix(),'title':' / '.join(names),'action_id':action,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size,
            'source_url':'https://wger.de/api/v2/video/'+str(vid)+'/','exercise_url':'https://wger.de/api/v2/exerciseinfo/'+str(item['exercise'])+'/','media_url':url,'author':item['license_author'],
            'license':'CC-BY-SA-4.0','copyright_status':license['full_name'],'terms_url':license['url'],'license_object':item,
            'restrictions':'Attribution and ShareAlike apply to shared adaptations; no claim that ML weights are exempt.','use_scope':'Local evaluation-only; not used in training or bundled/publicly redistributed weights',
            'group':'wger-author:'+item['license_author'],'partition':'diagnostic','role':'round3_new_publisher_probe','variant_review':'pending_visual_variant_and_phase_review',
            'ground_truth':'Exercise metadata only; no independent posture-error labels',**info}
        data['sources'].append(row);atomic_json(manifest,data);by[sid]=row;print(json.dumps({'source':sid,'action':action,'bytes':row['bytes'],**info}),flush=True)


if __name__=='__main__':main()
