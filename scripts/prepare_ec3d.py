"""固定EC3D公开直链下载；许可未声明，保留来源与实际授权范围。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from scripts.prepare_error_training_data import get
from fitness_app.research_extract import atomic_json
REPO='Jacoo-Zhao/3D-Pose-Based-Feedback-For-Physical-Exercises'
COMMIT='1c8d28ddc8a346634f9c86335e3a261f90d6b9b2'
DIRECTORY=ROOT/'data/datasets/open-source-correction/ec3d'
FILES=['readme.md','dataset.py','evaluation.py','utils.py','data/EC3D/data_3D.pickle']

def run():
    tree=json.loads(get('https://api.github.com/repos/'+REPO+'/git/trees/'+COMMIT+'?recursive=1',2000000))
    entries={row['path']:row for row in tree['tree'] if row['type']=='blob'}
    license_files=[name for name in entries if any(t in name.lower() for t in ('license','copying'))]
    rows=[]
    for name in FILES:
        record=entries[name]
        if record['size']>22000000:raise ValueError('EC3D单项超出已核查范围')
        relative='data_3D.pickle' if name.endswith('data_3D.pickle') else 'source/'+name
        path=project_path(DIRECTORY/relative)
        raw=path.read_bytes() if path.is_file() else get('https://raw.githubusercontent.com/'+REPO+'/'+COMMIT+'/'+name,record['size'])
        blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if len(raw)!=record['size'] or blob!=record['sha']:raise ValueError('EC3D固定资源身份不符：'+name)
        if not path.exists():
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('xb') as output:output.write(raw)
        rows.append({'source_path':name,'path':path.relative_to(ROOT).as_posix(),'bytes':len(raw),
            'sha256':hashlib.sha256(raw).hexdigest(),'git_blob_sha1':blob})
        print(name,len(raw),flush=True)
    atomic_json(DIRECTORY/'manifest.json',{'repo':REPO,'commit':COMMIT,'files':rows,
        'public_access':True,'requires_application':False,'license':'not_declared_in_checked_repository',
        'license_files_found':license_files,'redistribution_permission_verified':False,'commercial_permission_verified':False,
        'user_authorization':'用户2026-11-04明确要求排除申请制来源并下载公开可访问的许可未声明数据，用于当前本地训练研究',
        'scope':'original_3d_poses_and_labels_not_rgb_videos','runtime_enabled':False,
        'untrusted_repository_code_executed':False,'model_weights_downloaded':False})

if __name__=='__main__':run()
