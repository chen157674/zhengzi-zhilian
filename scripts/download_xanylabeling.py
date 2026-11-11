"""官方公开安装包分段续传；各段范围校验，完整SHA一致才生成可执行安装包。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import re
import sys
import threading
import time
from pathlib import Path
import requests

ROOT=Path(__file__).resolve().parents[1]
URL='https://github.com/CVHub520/X-AnyLabeling/releases/download/v4.2.2/X-AnyLabeling-v4.2.2-Windows-CPU-Setup.exe'
SIZE=349876199
SHA='cc5e16c539b4dab220ff5bbed7f966cedb7d0e4fc3b4c2a2e06cecbda565e221'
TARGET=Path('D:/software_installation_package/54_X-AnyLabeling/X-AnyLabeling-v4.2.2-Windows-CPU-Setup.exe')
STATE=ROOT/'data/verification/round3/xanylabeling-download.json'
CHUNK=4*1024*1024


def atomic(data):
    stage=STATE.with_suffix('.json.tmp')
    stage.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    stage.replace(STATE)


def main():
    partial=TARGET.with_suffix(TARGET.suffix+'.part')
    if TARGET.exists():
        if hashlib.file_digest(TARGET.open('rb'),'sha256').hexdigest()!=SHA:raise ValueError('已有安装包不匹配')
        print('安装包已校验，无需下载');return
    if not partial.is_file():raise ValueError('原续传文件不存在，不新建替代位置')
    owner=json.loads((ROOT/'data/verification/round3/xanylabeling-install.json').read_text(encoding='utf-8'))
    if owner['source']!=URL or owner['expected_sha256']!=SHA:raise ValueError('下载归属不符')
    if STATE.exists():
        state=json.loads(STATE.read_text(encoding='utf-8'))
        if state['url']!=URL or state['expected_sha256']!=SHA:raise ValueError('分段状态不匹配')
    else:
        state={'url':URL,'expected_sha256':SHA,'expected_bytes':SIZE,'prefix_bytes':partial.stat().st_size,'completed_ranges':[]}
        if not 0<=state['prefix_bytes']<=SIZE:raise ValueError('原文件范围无效')
        atomic(state)
    prefix=state['prefix_bytes'];completed={tuple(x) for x in state['completed_ranges']}
    ranges=[(start,min(start+CHUNK,SIZE)-1) for start in range(prefix,SIZE,CHUNK)]
    if not completed.issubset(set(ranges)):raise ValueError('续传分段边界无效')
    with partial.open('r+b') as stream:stream.truncate(SIZE)
    lock=threading.Lock();started=time.monotonic()
    def fetch(pair):
        start,end=pair
        for attempt in range(3):
            try:
                with requests.Session() as session:
                    session.trust_env=False
                    with session.get(URL,headers={'Range':f'bytes={start}-{end}','User-Agent':'fitness-annotation-setup'},stream=True,timeout=(20,90)) as response:
                        response.raise_for_status()
                        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                        if response.status_code!=206 or not match or tuple(map(int,match.groups()))!=(start,end,SIZE):
                            raise ValueError('服务器分段范围不符')
                        written=0
                        with partial.open('r+b') as output:
                            output.seek(start)
                            for chunk in response.iter_content(128*1024):
                                if written+len(chunk)>end-start+1:raise ValueError('分段超出预期')
                                output.write(chunk);written+=len(chunk)
                            output.flush()
                        if written!=end-start+1:raise ValueError('分段不完整')
                with lock:
                    completed.add(pair);state['completed_ranges']=[list(x) for x in sorted(completed)]
                    state['downloaded_bytes']=prefix+sum(e-s+1 for s,e in completed)
                    atomic(state)
                    print(json.dumps({'downloaded':state['downloaded_bytes'],'total':SIZE,'percent':round(state['downloaded_bytes']/SIZE*100,1),'elapsed_seconds':round(time.monotonic()-started)}),flush=True)
                return
            except Exception:
                if attempt==2:raise
    with ThreadPoolExecutor(max_workers=16) as pool:
        for task in as_completed(pool.submit(fetch,pair) for pair in ranges if pair not in completed):task.result()
    with partial.open('rb') as stream:actual=hashlib.file_digest(stream,'sha256').hexdigest()
    if actual!=SHA:raise ValueError('完整SHA不一致，不执行文件')
    partial.replace(TARGET);state['verified_sha256']=actual;state['phase']='verified';atomic(state)
    print(json.dumps({'phase':'verified','bytes':SIZE,'sha256':actual}),flush=True)


if __name__=='__main__':main()
