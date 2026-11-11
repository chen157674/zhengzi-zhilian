"""固定官方源码的按需下载；复用旧资源清单，不运行上游安装脚本。"""
from concurrent.futures import ThreadPoolExecutor,as_completed
import base64
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from scripts.prepare_pretrained_correction import WORK,REPOS,update,sha,request

SOURCES={
    'sports2d':('davidpagnon/Sports2D','e3b15be162182abbfe5d2084ac6b0d3f3d6099ed','BSD-3-Clause',''),
    'rtmlib':('Tau-J/rtmlib','03a1693e59e4f7cd84582c0fb30459b3bf18ad42','Apache-2.0',''),
    'learnopencv-fitness':('spmallick/learnopencv','ed110e23a176c353df797d5f08977cf9d4007675','not_declared_selected_directory','AI-Fitness-Trainer-Using-MediaPipe-Analyzing-Squats'),
    'motionbert':('Walter0807/MotionBERT','705d3a95354db8bdb696b3492e47a3b5537174ff','Apache-2.0',''),
    'opencap-core':('opencap-org/opencap-core','e5d29f12e725a2e14e6e4e85617834685f738753','Apache-2.0',''),
    'sam2':('facebookresearch/sam2','2b90b9f5ceec907a1c18123530e92e794ad901a4','Apache-2.0',''),
    'qwen25-vl':('QwenLM/Qwen2.5-VL','96588727e44c78b25ba03ea03b8e12f7e64fd0da','Apache-2.0',''),
    'pose2sim-filtering':('perfanalytics/Pose2Sim','5bce3d82096200ce248f17211b209b3723f9091a','BSD-3-Clause',''),
}
CODE={'.py','.pyi','.md','.rst','.txt','.toml','.yaml','.yml','.json','.cfg','.ini','.sh','.bat','.ps1','.cmd',
      '.cpp','.hpp','.cu','.h','.c','.js','.ts','.tsx','.jsx','.html','.css','.vue','.ipynb','.xml','.osim'}
# 已验证本机7897代理TLS失败而直连可用；仅本脚本改用直连，证书校验保持默认。
OPEN=urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get_json(url):
    for attempt in range(3):
        try:
            with OPEN.open(request(url),timeout=35) as stream:return json.load(stream)
        except urllib.error.HTTPError as error:
            if error.code in (403,429):raise
            if attempt==2:raise
            time.sleep(attempt+1)
        except (OSError,TimeoutError):
            if attempt==2:raise
            time.sleep(attempt+1)


def listing(repo,commit,prefix):
    base=f'https://api.github.com/repos/{repo}'
    if not prefix:
        tree=get_json(base+f'/git/trees/{commit}?recursive=1')
        if tree.get('truncated'):raise ValueError('上游源码树不完整')
        return [row for row in tree['tree'] if row['type']=='blob']
    entries=get_json(base+'/contents/'+urllib.parse.quote(prefix,safe='/')+'?ref='+commit)
    rows=[]
    for row in entries:
        if row['type']=='file':rows.append({'path':row['path'],'sha':row['sha'],'size':row['size'],'mode':'100644'})
        elif row['type']=='dir':
            tree=get_json(base+'/git/trees/'+row['sha']+'?recursive=1')
            if tree.get('truncated'):raise ValueError('健身示例子树不完整')
            rows.extend({**item,'path':row['path']+'/'+item['path']} for item in tree['tree'] if item['type']=='blob')
    for row in get_json(base+'/contents?ref='+commit):
        if row['type']=='file' and (row['name'].lower().startswith(('license','copying','notice')) or row['name']=='README.md'):
            rows.append({'path':row['path'],'sha':row['sha'],'size':row['size'],'mode':'100644'})
    return rows


def selected(name,row):
    path=Path(row['path']);suffix=path.suffix.lower();size=row.get('size',0)
    if name=='pose2sim-filtering':return row['path'] in {'Pose2Sim/filtering.py','Pose2Sim/common.py','LICENSE','README.md'}
    if name=='qwen25-vl' and row['path']=='cookbooks/mobile_agent.ipynb':return False
    if row.get('mode')=='120000':return False
    if size>5*1024*1024:return False
    if path.name.lower().startswith(('license','notice','copying')) or path.name in ('Dockerfile','Makefile','.gitignore'):return True
    if suffix in CODE:return True
    if name in ('sports2d','rtmlib') and suffix in ('.csv','.trc','.mot','.npy','.npz','.jpg','.png','.mp4'):
        return size<=3*1024*1024
    return False


def fetch_file(repo,commit,folder,row):
    target=project_path(folder/row['path']);target.relative_to(folder.resolve())
    if target.exists():
        payload=target.read_bytes()
        if hashlib.sha1(f'blob {len(payload)}\0'.encode()+payload).hexdigest()!=row['sha']:raise ValueError('已有源码被修改，不覆盖：'+row['path'])
    else:
        url=f'https://raw.githubusercontent.com/{repo}/{commit}/'+urllib.parse.quote(row['path'],safe='/')
        for attempt in range(3):
            try:
                with OPEN.open(request(url),timeout=40) as stream:payload=stream.read(5*1024*1024+1)
                break
            except (OSError,TimeoutError):
                if attempt==2:raise
                time.sleep(attempt+1)
        if len(payload)!=row['size'] or hashlib.sha1(f'blob {len(payload)}\0'.encode()+payload).hexdigest()!=row['sha']:
            # raw端点可能把LFS指针展开成大文件；源码研究保留经Git校验的原指针，不冒充取得实际大文件。
            original=get_json(f'https://api.github.com/repos/{repo}/git/blobs/'+row['sha']) if row['size']<512 else {}
            pointer=base64.b64decode(original.get('content',''))
            if not pointer.startswith(b'version https://git-lfs.github.com/spec/v1') or hashlib.sha1(f'blob {len(pointer)}\0'.encode()+pointer).hexdigest()!=row['sha']:
                raise ValueError('固定Git blob校验失败：'+row['path'])
            payload=pointer
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as stream:stream.write(payload)
    return {'path':row['path'],'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),
            'git_blob_sha1':row['sha'],'git_lfs_pointer':payload.startswith(b'version https://git-lfs.github.com/spec/v1')}


def prepare(name):
    repo,commit,license_name,prefix=SOURCES[name];folder=project_path(REPOS/name)
    manifest=json.loads((WORK/'manifest.json').read_text(encoding='utf-8'));old=manifest.get('repos',{}).get(name)
    if name=='qwen25-vl' and old and old.get('commit')==commit and old.get('state')=='failed' and {f['path'] for f in old.get('failures',[])}=={'cookbooks/mobile_agent.ipynb'}:
        for item in old['assets']:
            if sha(folder/item['path'])!=item['sha256']:raise ValueError('已下载Qwen源码被修改')
        old.update(state='ready',failures=[],omitted_nonfitness_lfs_examples=['cookbooks/mobile_agent.ipynb'],
                   scope='selected_source_configs_licenses_excluding_nonfitness_mobile_agent_lfs_notebook')
        update('repos',name,old)
        return {'repo':name,'files':len(old['assets']),'omitted_nonfitness_lfs_notebook':True}
    if name=='pose2sim-filtering' and not old:
        # Sports2D已固定此依赖提交；API额度耗尽时从固定提交HTTPS读取四个已知文件。
        assets=[];folder.mkdir(parents=True,exist_ok=True)
        for relative in ('Pose2Sim/filtering.py','Pose2Sim/common.py','LICENSE','README.md'):
            path=project_path(folder/relative);path.relative_to(folder.resolve())
            with OPEN.open(request(f'https://raw.githubusercontent.com/{repo}/{commit}/'+relative),timeout=35) as stream:payload=stream.read(5*1024*1024+1)
            if len(payload)>5*1024*1024:raise ValueError('依赖源码超出计划')
            if path.exists():raise ValueError('已有未登记依赖文件，不覆盖')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
            assets.append({'path':relative,'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest(),
                'verification':'fixed_commit_https_and_local_sha256_api_rate_limited'})
        update('repos',name,{'state':'ready','repo':repo,'commit':commit,'license':license_name,'path':folder.relative_to(ROOT).as_posix(),
            'scope':'sports2d_pinned_filtering_dependency_four_files','assets':assets,'uncompressed_bytes':sum(a['bytes'] for a in assets),
            'verification':'fixed_commit_https_and_local_sha256_not_independent_git_blob_match'})
        return {'repo':name,'files':len(assets),'verification':'fixed_commit_https_sha256'}
    if old and old.get('state')=='ready':
        if old['commit']!=commit:raise ValueError('已登记提交不同，不覆盖')
        for item in old['assets']:
            if sha(folder/item['path'])!=item['sha256']:raise ValueError('已登记源码摘要变化')
        return {'repo':name,'files':len(old['assets']),'reused':True}
    if folder.exists() and (not old or old.get('commit')!=commit):raise ValueError('已有未登记源码目录，不覆盖')
    rows=listing(repo,commit,prefix);chosen=[row for row in rows if selected(name,row)]
    total=sum(row['size'] for row in chosen)
    if not chosen or total>100*1024*1024:raise ValueError('所选源码范围为空或超出计划')
    record={'repo':repo,'commit':commit,'license':license_name,'path':folder.relative_to(ROOT).as_posix(),
            'scope':'selected_source_configs_licenses_and_small_reproduction_inputs','selected_prefix':prefix or None,
            'upstream_blob_count':len(rows),'excluded_blobs':len(rows)-len(chosen),'state':'downloading','assets':[]}
    update('repos',name,record);folder.mkdir(parents=True,exist_ok=True)
    assets=[];failures=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs={pool.submit(fetch_file,repo,commit,folder,row):row['path'] for row in chosen}
        for job in as_completed(jobs):
            try:assets.append(job.result())
            except Exception as error:failures.append({'path':jobs[job],'error':str(error)})
    record.update(state='failed' if failures else 'ready',assets=sorted(assets,key=lambda a:a['path']),
                  uncompressed_bytes=sum(a['bytes'] for a in assets),failures=failures)
    update('repos',name,record)
    if failures:raise RuntimeError(name+'有'+str(len(failures))+'个文件未取得，已保留可校验部分供续传')
    return {'repo':name,'files':len(assets),'mib':round(record['uncompressed_bytes']/2**20,2),'excluded_blobs':record['excluded_blobs']}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('names',nargs='*',choices=list(SOURCES));args=parser.parse_args()
    failed=[]
    for name in args.names or SOURCES:
        try:print(json.dumps(prepare(name),ensure_ascii=False),flush=True)
        except Exception as error:failed.append(name);print(json.dumps({'repo':name,'error':str(error)},ensure_ascii=False),flush=True)
    if failed:raise SystemExit('源码未完整取得：'+','.join(failed))
