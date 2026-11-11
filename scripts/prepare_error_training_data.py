"""取得固定MIT来源的纠错坐标训练集，复用已有同摘要资源。"""
import hashlib,json,sys,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json
REPO='NgoQuocBao1010/Exercise-Correction'
COMMIT='202a0a802d8d2e3ea42f00e6a8c47da9cafc09d7'
DEST=ROOT/'data/datasets/open-source-correction/exercise-correction'
FILES=['LICENSE','README.md','core/plank_model/train.csv','core/plank_model/test.csv',
    'core/plank_model/README.md','core/plank_model/1.data.ipynb','core/bicep_model/train.csv',
    'core/bicep_model/test.csv','core/bicep_model/README.md','core/bicep_model/1.data.ipynb']

def get(url,maximum):
    request=urllib.request.Request(url,headers={'User-Agent':'fitness-assistant-error-training'})
    for direct in (False,True):
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
            with opener.open(request,timeout=60) as response:data=response.read(maximum+1)
            if len(data)>maximum:raise ValueError('下载超过范围')
            return data
        except (OSError,TimeoutError):
            if direct:raise

def run():
    tree=json.loads(get('https://api.github.com/repos/'+REPO+'/git/trees/'+COMMIT+'?recursive=1',2000000))
    blobs={x['path']:x for x in tree['tree'] if x['type']=='blob'};files=[]
    for name in FILES:
        row=blobs[name]
        if row['size']>40000000:raise ValueError('单项大小超出已核查范围')
        existing=[DEST/name,ROOT/'third_party/Exercise-Correction'/name]
        if name=='core/plank_model/test.csv':existing.insert(0,ROOT/'data/models/open-source-correction/plank-reference/test.csv')
        source=next((p for p in existing if p.is_file() and p.stat().st_size==row['size'] and
            hashlib.sha1(b'blob '+str(row['size']).encode()+b'\0'+p.read_bytes()).hexdigest()==row['sha']),None)
        if source is None:
            raw=get('https://raw.githubusercontent.com/'+REPO+'/'+COMMIT+'/'+name,row['size'])
            if len(raw)!=row['size'] or hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=row['sha']:raise ValueError('固定Git资源身份不一致')
            source=project_path(DEST/name);source.parent.mkdir(parents=True,exist_ok=True)
            with source.open('xb') as stream:stream.write(raw)
        files.append({'source_path':name,'path':source.relative_to(ROOT).as_posix(),'bytes':source.stat().st_size,
            'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'git_blob_sha1':row['sha']})
        print(name,source.stat().st_size,flush=True)
    atomic_json(DEST/'manifest.json',{'repo':REPO,'commit':COMMIT,'license':'MIT','files':files,
        'source_subject_ids_available':False,'source_video_ids_available':False,
        'source_test_is_not_project_frozen_test':True,'training_authorization':'用户2026-11-04要求下载公开错误动作数据并训练',
        'labels':{'plank':{'C':'correct','H':'hips_high','L':'hips_low'},'bicep':{'C':'correct','L':'lean_back'}},
        'scope':'original_mediapipe_coordinate_rows_not_original_videos','runtime_enabled':False})

if __name__=='__main__':run()
