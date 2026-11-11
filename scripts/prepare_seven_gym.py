"""补取已使用的 CC BY 4.0 数据集中的肩推、臂屈伸；沿用原人员分区。"""
import hashlib,json,re,sys,time,urllib.request
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json

DIRECTORY=ROOT/'data/datasets/development-movements'
METADATA=DIRECTORY/'seven-exercises-expansion.json'
SOURCE='https://data.mendeley.com/datasets/kgbb3yn47p/1'
API='https://data.mendeley.com/public-api/datasets/kgbb3yn47p'
ACTIONS={'shoulder':'dumbbell_shoulder_press','tricep':'overhead_dumbbell_extension'}
TRAIN={'001','002','007','009','011','021'}
VALIDATION={'004','016','018','019'}


def get(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    return urllib.request.urlopen(request,timeout=60)


def inventory():
    if METADATA.exists():return json.loads(METADATA.read_text(encoding='utf-8'))
    with get(API+'/folders/1') as response:folders=json.load(response)
    by={r['id']:r for r in folders}
    def full(row):return (full(by[row['parent_id']])+'/' if row.get('parent_id') else '')+row['name']
    selected=[r for r in folders if re.fullmatch(r'Dataset Exercise-wise/(shoulder|tricep)/(good|bad)/(front|side|diagonal)',full(r))]
    rows=[]
    for folder in selected:
        with get(API+f"/files?folder_id={folder['id']}&version=1&$limit=1000") as response:files=json.load(response)
        for row in files:
            action,quality,view=full(folder).split('/')[1:]
            row.update(source_action=action,quality=quality,view=view)
            rows.append(row)
        time.sleep(.25)
    result={'source_url':SOURCE,'license':'CC BY 4.0','version':1,'files':rows}
    atomic_json(METADATA,result);return result


def download(row):
    details=row['content_details'];digest=details['sha256_hash']
    target=project_path(DIRECTORY/('seven-'+row['filename']))
    if target.exists():
        if target.stat().st_size!=details['size'] or hashlib.sha256(target.read_bytes()).hexdigest()!=digest:raise ValueError('已有数据文件摘要不同')
        return row,target,True
    part=target.with_suffix('.mp4.part')
    if part.exists():raise ValueError('已有未归属本次下载的 part，请先核查')
    owned=False
    try:
        with get(details['download_url']) as response,part.open('xb') as output:
            owned=True;sha=hashlib.sha256();size=0
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>details['size']:raise ValueError('源文件大小超出声明')
                sha.update(block);output.write(block)
        if size!=details['size'] or sha.hexdigest()!=digest:raise ValueError('源文件摘要不同')
        part.replace(target)
    finally:
        if owned and part.exists():part.unlink()
    return row,target,False


def run():
    metadata=inventory();by_hash={}
    for row in metadata['files']:
        match=re.fullmatch(r'subject_(\d+)_(shoulder|tricep)_(good|bad)_(front|side|diagonal)\.mp4',row['filename'])
        if not match:continue
        row['subject']=match[1];by_hash.setdefault(row['content_details']['sha256_hash'],[]).append(row)
    selected=[];excluded=[]
    for digest,aliases in by_hash.items():
        subjects={r['subject'] for r in aliases}
        if not subjects&(TRAIN|VALIDATION):continue
        partitions={'train' if s in TRAIN else 'validation' if s in VALIDATION else 'excluded' for s in subjects}
        if len(partitions)!=1 or 'excluded' in partitions or len({r['source_action'] for r in aliases})!=1:
            excluded.append({'sha256':digest,'reason':'conflicting_subject_partition_or_action','filenames':[r['filename'] for r in aliases]});continue
        row=aliases[0].copy();row['partition']=partitions.pop();row['aliases']=[r['filename'] for r in aliases];selected.append(row)
    print(json.dumps({'selected':len(selected),'bytes':sum(r['content_details']['size'] for r in selected),'excluded':len(excluded)}),flush=True)
    source_path=DIRECTORY/'sources.json'
    with ThreadPoolExecutor(max_workers=2) as executor:
        for future in as_completed([executor.submit(download,row) for row in selected]):
            row,path,reused=future.result();details=row['content_details'];key='seven-'+details['sha256_hash'][:16]
            manifest=json.loads(source_path.read_text(encoding='utf-8'))
            entry={'id':key,'path':path.relative_to(ROOT).as_posix(),'sha256':details['sha256_hash'],'bytes':details['size'],
                   'source_url':SOURCE,'download_url':details['download_url'],'source_file_id':row['id'],
                   'source_dataset':'kgbb3yn47p','source_version':1,'author':'S Ashlesh Pai; Pratham PN; Advith P; Abhishek Mashetty',
                   'title':row['filename'],'license':'CC BY 4.0','license_url':'https://creativecommons.org/licenses/by/4.0/',
                   'action_id':ACTIONS[row['source_action']],'source_group':'kgbb3yn47p:subject_'+row['subject'],
                   'partition':row['partition'],'source_subject':row['subject'],'source_quality':row['quality'],'source_view':row['view'],
                   'aliases':row['aliases'],'ground_truth':'Publisher exercise and coarse good/bad only; no independent specific-error labels',
                   'awaiting_segment_review':True,'use_scope':'Local recognition development; retain attribution; identify extracted segments; no endorsement'}
            if not any(r['id']==key for r in manifest['sources']):manifest['sources'].append(entry);atomic_json(source_path,manifest)
            print(json.dumps({'id':key,'action':entry['action_id'],'partition':row['partition'],'reused':reused}),flush=True)
    metadata['excluded']=excluded;metadata['selected_hashes']=[r['content_details']['sha256_hash'] for r in selected];atomic_json(METADATA,metadata)


if __name__=='__main__':run()
