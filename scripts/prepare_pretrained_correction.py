"""固定来源的第三方纠错代码与权重准备；下载不执行仓库代码。"""
import argparse
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import sys
import threading
import time
import urllib.request
import urllib.parse
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json as _atomic_json

WORK=ROOT/'data/experiments/open-source-correction'
REPOS=WORK/'repos'
MANIFEST=WORK/'manifest.json'
SOURCES={
    'exercise-pose-feedback':('PSLeon24/AI_Exercise_Pose_Feedback','041ceeb6cf6263311cabb1b76344c3695b434bd7','not_declared'),
    'sitrex':('asellam/sitrex','1eb3d7d431a6b52ba07afca0cde55d9f33822d43','MIT'),
    'mmpose':('open-mmlab/mmpose','759b39c13fea6ba094afc1fa932f51dc1b11cbf9','Apache-2.0'),
}
MODELS={
    'rtmpose-body': 'rtmpose-s_simcc-body7_pt-body7_420e-256x192-acd4a1ef_20230504.zip',
    'rtmdet-hand': 'rtmdet_nano_8xb32-300e_hand-267f9c8f.zip',
    'rtmpose-hand': 'rtmpose-m_simcc-hand5_pt-aic-coco_210e-256x256-74fb594_20230320.zip',
}
LOCK=threading.Lock()


def atomic_json(path,value):
    for attempt in range(8):
        try:return _atomic_json(path,value)
        except PermissionError:
            if attempt==7:raise
            time.sleep(.025*(attempt+1))


def sha(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def update(section,name,value):
    with LOCK:
        document=json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {
            'schema_version':1,'owner':'fitness-pretrained-correction','private_media_upload_allowed':False}
        if document.get('owner')!='fitness-pretrained-correction':raise ValueError('已有资源清单归属不符')
        document.setdefault(section,{})[name]=value;atomic_json(MANIFEST,document)


def request(url):
    return urllib.request.Request(url,headers={'User-Agent':'fitness-local-adaptation/1'})


def download(url,target,maximum,expected_sha256=None):
    target=project_path(target);target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if expected_sha256 and sha(target)==expected_sha256:return
        raise ValueError('已有下载文件，不覆盖：'+str(target))
    partial=target.with_suffix(target.suffix+'.part');owned=False
    try:
        with urllib.request.urlopen(request(url),timeout=45) as response,partial.open('xb') as output:
            owned=True;count=0;last_report=0;checksum=hashlib.sha256()
            while chunk:=response.read(1024*1024):
                count+=len(chunk)
                if count>maximum:raise ValueError('下载超过计划大小限制')
                output.write(chunk);checksum.update(chunk)
                if count-last_report>=32*1024*1024:
                    print(json.dumps({'download':target.name,'mib':round(count/2**20,1)}),flush=True);last_report=count
            if count==0:raise ValueError('下载为空')
            if response.headers.get('Content-Length') and count!=int(response.headers['Content-Length']):raise ValueError('下载不完整')
            if expected_sha256 and checksum.hexdigest()!=expected_sha256:raise ValueError('下载SHA不符')
        partial.replace(target)
    finally:
        if owned and partial.exists():partial.unlink()


def prepare_repo(name):
    repo,commit,license_name=SOURCES[name];destination=project_path(REPOS/name)
    with LOCK:
        previous=json.loads(MANIFEST.read_text(encoding='utf-8')).get('repos',{}).get(name) if MANIFEST.exists() else None
    if previous and previous.get('state')=='ready':
        if previous['commit']!=commit or not destination.is_dir():raise ValueError('已有源码版本或目录不符')
        for item in previous['assets']:
            if sha(destination/item['path'])!=item['sha256']:raise ValueError('已有模型/数据/许可文件变化')
        return {'repo':name,'reused':True,'state':'ready'}
    if destination.exists():raise ValueError('已有未登记源码目录，不覆盖')
    staging=project_path(REPOS/(name+'.extracting'))
    if staging.exists():raise ValueError('已有提取中间目录，请先核查归属')
    archive=project_path(WORK/(name+'.zip'))
    url=f'https://codeload.github.com/{repo}/zip/{commit}'
    update('repos',name,{'state':'downloading','repo':repo,'commit':commit,'license':license_name,'url':url})
    archive_owned=False;staging_owned=False
    try:
        with urllib.request.urlopen(request(f'https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1'),timeout=35) as response:tree=json.load(response)
        if tree.get('truncated'):raise ValueError('源码清单不完整')
        files={row['path']:row for row in tree['tree'] if row['type']=='blob'}
        download(url,archive,550*1024*1024);archive_owned=True;archive_sha=sha(archive)
        staging.mkdir(parents=True);staging_owned=True
        assets=[];total=0;deduplicated=0;known={};seen=set()
        with zipfile.ZipFile(archive) as bundle:
            prefixes={PurePosixPath(info.filename).parts[0] for info in bundle.infolist()}
            if len(prefixes)!=1:raise ValueError('源码归档根目录异常')
            for info in bundle.infolist():
                if info.is_dir():continue
                parts=PurePosixPath(info.filename).parts[1:]
                if not parts or any(part in ('..','') for part in parts):raise ValueError('归档路径异常')
                relative=PurePosixPath(*parts).as_posix();entry=files.get(relative)
                if entry is None or entry['size']!=info.file_size:raise ValueError('归档与固定提交清单不符：'+relative)
                target=(staging/Path(*parts)).resolve();target.relative_to(staging)
                target.parent.mkdir(parents=True,exist_ok=True)
                total+=info.file_size
                if total>650*1024*1024:raise ValueError('源码展开超过计划上限')
                git_hash=hashlib.sha1(f'blob {info.file_size}\0'.encode());checksum=hashlib.sha256()
                # 源码符号链接只存其原始文字，不创建Windows链接。
                with bundle.open(info) as source,target.open('xb') as output:
                    while chunk:=source.read(1024*1024):output.write(chunk);git_hash.update(chunk);checksum.update(chunk)
                if git_hash.hexdigest()!=entry['sha']:raise ValueError('源码Git blob摘要不符：'+relative)
                digest=checksum.hexdigest();seen.add(relative)
                if digest in known and entry['mode']!='120000':
                    first=known[digest]
                    target.unlink()
                    try:os.link(first,target);deduplicated+=info.file_size
                    except OSError:shutil.copyfile(first,target)
                else:known[digest]=target
                if target.suffix.lower() in ('.h5','.pkl','.pickle','.pt','.pth','.onnx','.csv','.npz','.mp4') or 'license' in target.name.lower():
                    assets.append({'path':relative,'bytes':info.file_size,'sha256':digest})
        if seen!=set(files):raise ValueError('归档缺少固定提交文件')
        staging.rename(destination);staging_owned=False
        update('repos',name,{'state':'ready','repo':repo,'commit':commit,'license':license_name,'url':url,
            'path':destination.relative_to(ROOT).as_posix(),'archive_sha256':archive_sha,'blob_count':len(seen),
            'uncompressed_bytes':total,'deduplicated_hardlink_bytes':deduplicated,'assets':assets})
        return {'repo':name,'state':'ready','files':len(seen),'mib':round(total/2**20,2),'hardlink_saved_mib':round(deduplicated/2**20,2)}
    except Exception as error:
        update('repos',name,{'state':'failed','repo':repo,'commit':commit,'error':str(error)})
        raise
    finally:
        if archive_owned and archive.exists():archive.unlink()
        if staging_owned and staging.exists():
            resolved=staging.resolve();resolved.relative_to(REPOS.resolve())
            if resolved.name!=name+'.extracting':raise ValueError('临时源码清理边界不符')
            shutil.rmtree(resolved)


def repos():
    REPOS.mkdir(parents=True,exist_ok=True)
    failures=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(prepare_repo,name):name for name in SOURCES}
        for future in as_completed(futures):
            try:print(json.dumps(future.result(),ensure_ascii=False),flush=True)
            except Exception as error:
                failures.append(futures[future]);print(json.dumps({'repo':futures[future],'error':str(error)},ensure_ascii=False),flush=True)
    if failures:raise RuntimeError('源码准备失败：'+','.join(failures))


def prepare_model(name):
    filename=MODELS[name];base=project_path(ROOT/'data/models/open-source-correction');base.mkdir(exist_ok=True)
    target=base/name
    with LOCK:
        previous=json.loads(MANIFEST.read_text(encoding='utf-8')).get('models',{}).get(name) if MANIFEST.exists() else None
    if previous and previous.get('state')=='ready':
        for item in previous['files']:
            if sha(project_path(item['path']))!=item['sha256']:raise ValueError('已下载SDK模型变化')
        return {'model':name,'state':'ready','reused':True}
    if target.exists():raise ValueError('已有未登记模型目录，不覆盖')
    archive=project_path(WORK/filename);owned=False;target_owned=False
    url='https://download.openmmlab.com/mmpose/v1/projects/rtmposev1/onnx_sdk/'+filename
    readme=(REPOS/'mmpose/projects/rtmpose/README.md').read_text(encoding='utf-8')
    if url not in readme:raise ValueError('模型地址不在已固定的官方README内')
    update('models',name,{'state':'downloading','url':url,'upstream':'open-mmlab/mmpose','source_commit':SOURCES['mmpose'][1]})
    try:
        for attempt in range(3):
            try:download(url,archive,250*1024*1024);owned=True;break
            except (OSError,TimeoutError):
                if attempt==2:raise
                time.sleep(attempt+1)
        archive_sha=sha(archive);target.mkdir();target_owned=True;files=[];total=0
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                if info.is_dir():continue
                parts=PurePosixPath(info.filename).parts
                if PurePosixPath(info.filename).is_absolute() or '..' in parts:raise ValueError('模型归档路径不符')
                out=(target/Path(*parts)).resolve();out.relative_to(target)
                total+=info.file_size
                if total>300*1024*1024:raise ValueError('SDK展开超出预期')
                out.parent.mkdir(parents=True,exist_ok=True)
                with bundle.open(info) as source,out.open('xb') as output:shutil.copyfileobj(source,output)
                files.append({'path':out.relative_to(ROOT).as_posix(),'bytes':out.stat().st_size,'sha256':sha(out)})
        onnx=[item for item in files if item['path'].endswith('.onnx')]
        if len(onnx)!=1:raise ValueError('SDK的ONNX模型不唯一')
        update('models',name,{'state':'ready','url':url,'archive_sha256':archive_sha,'files':files,
            'onnx':onnx[0]['path'],'runtime_validated':False,'production_enabled':False,
            'source_commit':SOURCES['mmpose'][1],'model_license':'see_upstream_and_training_dataset_terms'})
        return {'model':name,'state':'ready','mib':round(total/2**20,2)}
    except Exception as error:
        update('models',name,{'state':'failed','url':url,'error':str(error)})
        if target_owned and target.exists():
            target.resolve().relative_to(base.resolve());shutil.rmtree(target)
        raise
    finally:
        if owned and archive.exists():archive.unlink()


def models():
    failures=[]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(prepare_model,name):name for name in MODELS}
        for future in as_completed(futures):
            try:print(json.dumps(future.result(),ensure_ascii=False),flush=True)
            except Exception as error:
                failures.append(futures[future]);print(json.dumps({'model':futures[future],'error':str(error)},ensure_ascii=False),flush=True)
    if failures:raise RuntimeError('预训练SDK准备失败：'+','.join(failures))


def sitrex_data():
    with LOCK:
        previous=json.loads(MANIFEST.read_text(encoding='utf-8')).get('datasets',{}).get('sitrex') if MANIFEST.exists() else None
    if previous and previous['state']=='ready':
        for item in previous['files']:
            if sha(project_path(item['path']))!=item['sha256']:raise ValueError('已有SiTrEx数据变化')
        print(json.dumps({'dataset':'sitrex','reused':True,'files':len(previous['files'])}),flush=True);return
    api='https://www.kaggle.com/api/v1/datasets/'
    with urllib.request.urlopen(request(api+'view/abdellah213/sitrex-dataset'),timeout=30) as response:metadata=json.load(response)
    if metadata['currentVersionNumber']!=2 or metadata['licenseName']!='CC0: Public Domain' or metadata['isPrivate']:
        raise ValueError('SiTrEx数据版本/公开性/许可与已核查内容不同')
    with urllib.request.urlopen(request(api+'list/abdellah213/sitrex-dataset?pageSize=200&datasetVersionNumber=2'),timeout=30) as response:listing=json.load(response)
    files=listing['datasetFiles'];selected=[]
    for exercise in ('bench press','deadlift','chest fly machine'):
        matched=sorted((row for row in files if row['name'].startswith(f'dataset/{exercise}_json/')),key=lambda row:row['name'])
        if len(matched)<2:raise ValueError('匹配动作数据清单不足')
        selected+=matched[:2]
    selected+=[next(row for row in files if row['name']=='dataset/exercise_angles.json')]
    folder=project_path(ROOT/'data/datasets/open-source-correction/sitrex');output=[]
    for item in selected:
        relative=PurePosixPath(item['name']);target=(folder/Path(*relative.parts)).resolve();target.relative_to(folder)
        url=api+'download/abdellah213/sitrex-dataset?'+urllib.parse.urlencode({'fileName':item['name'],'datasetVersionNumber':2})
        download(url,target,20*1024*1024)
        if target.stat().st_size!=item['totalBytes']:raise ValueError('SiTrEx文件字节数与上游清单不符')
        document=json.loads(target.read_text(encoding='utf-8'))
        if target.name!='exercise_angles.json' and not document.get('frames'):raise ValueError('SiTrEx姿态数据为空')
        output.append({'upstream_path':item['name'],'path':target.relative_to(ROOT).as_posix(),
                       'bytes':target.stat().st_size,'sha256':sha(target),'url':url})
    update('datasets','sitrex',{'state':'ready','dataset':'abdellah213/sitrex-dataset','version':2,
        'license':'CC0: Public Domain','upstream_total_bytes':metadata['totalBytes'],'scope':'selected_reproduction_subset',
        'selection':'first_two_lexicographic_files_per_bench_press_deadlift_chest_fly_machine_plus_angle_metadata',
        'files':output,'independent_quality_labels':False})
    print(json.dumps({'dataset':'sitrex','files':len(output),'mib':round(sum(row['bytes'] for row in output)/2**20,2)}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=['repos','models','sitrex-data']);args=parser.parse_args()
    repos() if args.stage=='repos' else models() if args.stage=='models' else sitrex_data()
