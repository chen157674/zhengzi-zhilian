"""补充官网公开播放且明确Public Domain的弯举视频；不访问登录下载或改原分段。"""
import hashlib,html,json,re,sys
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import urlparse
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
from fitness_app.research_extract import atomic_json
import av

DIRECTORY=ROOT/'data/datasets/development-movements'
SOURCES=[('636924','dumbbell-hammer-curl','hammer_curl','Dumbbell Hammer Curl','181101-M-JT909-022'),
 ('679653','dumbbell-bicep-curl','standing_dumbbell_curl','Dumbbell Bicep Curl','190510-M-JT909-007'),
 ('679657','dumbbell-bicep-curl','unknown','Dumbbell Bicep Curl','190510-M-JT909-008'),
 ('636923','dumbbell-curl','standing_dumbbell_curl','Dumbbell Curl','181101-M-JT909-021')]


def verified_variant(row):
    result=dict(row)
    if row['id']=='dvids-679657':
        if row['sha256']!='9952c49a6b042ec78557e4a9c63f607e19f7e606f823b2e3862d00251120b3c9':
            raise ValueError('已复核的上拉原片身份变化，不能沿用旧变式结论')
        result.update(action_id='unknown',publisher_action_id='standing_dumbbell_curl',role='out_of_catalogue_variant',
            variant_review='confirmed_db_pullover_not_dumbbell_curl',review_evidence_path='data/verification/round3/new-curl-sources.jpg',
            variant_review_basis='Codex exact raw frames and burned-in DB PULLOVER title; not independent posture truth')
    return result


def duplicate_reference(row,sources):
    canonical=next((r for r in sources if r['id']!=row['id'] and r.get('sha256')==row['sha256'] and not r.get('duplicate_of')),None)
    if canonical is None:return dict(row)
    return {**row,'path':canonical['path'],'duplicate_of':canonical['id'],'exclude_from_training':True,
            'role':'duplicate_source_reference_only','duplicate_basis':'identical original media SHA256; not a new recording or independent source'}


def read(url):
    with urlopen(Request(url,headers={'User-Agent':'fitness-assistant-research/1.0'}),timeout=45) as response:return response.read()


def main():
    manifest=DIRECTORY/'sources.json';data=json.loads(manifest.read_text(encoding='utf-8'));by={r['id']:r for r in data['sources']}
    for video_id,slug,action,title,virin in SOURCES:
        sid='dvids-'+video_id;target=project_path(DIRECTORY/(slug+'-dvids-'+video_id+'.mp4'))
        if sid in by:
            row=by[sid];path=project_path(row['path'])
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('已登记视频身份不一致，未重下载/覆盖')
            reviewed=verified_variant(row)
            if reviewed!=row:
                data['sources'][data['sources'].index(row)]=reviewed;atomic_json(manifest,data);by[sid]=reviewed
            print(json.dumps({'source':sid,'reused':True,'action_id':reviewed['action_id']}),flush=True);continue
        if target.exists():raise ValueError('目标文件未登记，先核实归属，不覆盖')
        page='https://www.dvidshub.net/video/'+video_id+'/'+slug;text=read(page).decode('utf-8')
        if 'PUBLIC DOMAIN' not in text or title.lower() not in text.lower() or virin not in text:raise ValueError('源页许可/身份未通过')
        urls=sorted(set(html.unescape(v.replace('\\/','/')) for v in re.findall(r'https?[^\s"<>]+\.mp4(?:\?[^\s"<>]*)?',text)))
        if len(urls)!=1 or urlparse(urls[0]).hostname!='d34w7g4gy10iej.cloudfront.net':raise ValueError('公开播放源不唯一或域名变更，停止自动下载')
        temporary=target.with_suffix('.part.mp4');owned=False
        try:
            with temporary.open('xb') as output:
                owned=True
                with urlopen(Request(urls[0],headers={'User-Agent':'fitness-assistant-research/1.0'}),timeout=60) as response:
                    length=0
                    while chunk:=response.read(1024*1024):
                        length+=len(chunk)
                        if length>64*1024*1024:raise ValueError('公开素材超过本次下载上限')
                        output.write(chunk)
            with av.open(str(temporary)) as video:
                stream=video.streams.video[0];duration=round(float(video.duration)/1000)
                metadata={'duration_ms':duration,'width':stream.width,'height':stream.height}
            if not 1000<=duration<=60000:raise ValueError('素材时长与短示范不匹配')
            media_digest=hashlib.sha256(temporary.read_bytes()).hexdigest()
            known=next((r for r in data['sources'] if r.get('sha256')==media_digest and not r.get('duplicate_of')),None)
            if known is not None:
                reused=project_path(known['path'])
                if not reused.is_file() or hashlib.sha256(reused.read_bytes()).hexdigest()!=media_digest:raise ValueError('重复媒体的原登记文件身份不一致')
                temporary.unlink();owned=False;target=reused
            else:temporary.replace(target)
        finally:
            if owned and temporary.exists():temporary.unlink()
        row={'id':sid,'title':title,'action_id':action,'source_url':page,'media_url':urls[0],'path':target.relative_to(ROOT).as_posix(),
            'author':'Capt. Matthew Holfinger / U.S. Marine Corps Training and Education Command','virin':virin,
            'copyright_status':'PUBLIC DOMAIN explicitly marked on asset page; subject to DVIDS copyright restrictions','terms_url':'https://www.dvidshub.net/about/copyright',
            'restrictions':'Retain credit; no implied endorsement; privacy/publicity and marks not waived.','use_scope':'Local engineering research; no marketing or implied endorsement',
            'role':'round3_grip_training_candidate','group':'public_dvids_collection','partition':'train','variant_review':'pending_visual_grip_and_active_interval_review',
            'ground_truth':'Publisher exercise description only; no independent specific-error labels','sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'bytes':target.stat().st_size,
            'source_page_sha256':hashlib.sha256(text.encode()).hexdigest(),**metadata}
        row=duplicate_reference(verified_variant(row),data['sources']);data['sources'].append(row);atomic_json(manifest,data);by[sid]=row
        print(json.dumps({'source':sid,'bytes':row['bytes'],**metadata},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
