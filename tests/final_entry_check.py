"""19正式start.cmd入口的新组合检查；生产配置配隔离测试库，不改个人资料。"""
import hashlib
from importlib.metadata import version
import platform
import re
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
from fitness_app.database import Database,json_text
from fitness_app.auth import initialize_admin
from fitness_app import db_schema as s

OUT=ROOT/'data/verification/step-19/entry-check.json'
TEMP=project_path('.tmp/tests/delivery-19')


def app_identity():
    files=[p for p in (ROOT/'fitness_app').rglob('*') if p.is_file() and p.suffix in {'.py','.js','.html','.css'}]
    files += [ROOT/p for p in ('start.cmd','scripts/start.ps1','scripts/environment.ps1','requirements.lock.txt','data/presentation-assets.json','data/models/manifest.json','data/models/cycle-counting.json','data/models/barbell-row.json')]
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    return hashlib.sha256(json_text(hashes).encode()).hexdigest(),hashes


class Client:
    def __init__(self,origin):self.origin=origin;self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self,path,method='GET',data=None,raw=None,headers=None):
        h=dict(headers or {})
        if method!='GET':
            _,token=self.request('/api/v1/auth/csrf');h['X-CSRFToken']=token['data']['csrf_token'];h['Idempotency-Key']=s.new_id()
        if data is not None:raw=json.dumps(data).encode();h['Content-Type']='application/json'
        req=urllib.request.Request(self.origin+path,data=raw,method=method,headers=h)
        try:response=self.opener.open(req,timeout=20)
        except urllib.error.HTTPError as e:response=e
        with response:
            content=response.read();result=json.loads(content) if 'application/json' in response.headers.get('Content-Type','') else content.decode('utf-8',errors='replace')
            return response.status,result
    def api(self,path,method='GET',data=None):
        code,result=self.request(path,method,data)
        if code>=400:raise RuntimeError(f'{path}: HTTP {code}, {result.get("error",{}).get("code")}')
        return result['data']


def main():
    identity,hashes=app_identity()
    environment={'python':platform.python_version(),'platform':platform.platform(),'packages':{name:version(name) for name in re.findall(r'^([A-Za-z0-9_.-]+)==', (ROOT/'requirements.lock.txt').read_text(encoding='utf-8'),re.M)}}
    if OUT.exists():
        old=json.loads(OUT.read_text(encoding='utf-8'))
        if old.get('checks')=='passed' and old.get('app_identity')==identity and old.get('environment')==environment:
            print(json.dumps({'cache_hit':True,'scope':old['scope'],'source_unchanged':True}));return
    if TEMP.exists():raise RuntimeError('交付测试目录已存在，先核归属，不覆盖')
    TEMP.mkdir();runtime=TEMP/'runtime';db_path=TEMP/'app.sqlite3';env=os.environ.copy()
    env.update(FITNESS_DATABASE_PATH=str(db_path),FITNESS_RUNTIME_DIR=str(runtime),TEMP=str(TEMP),TMP=str(TEMP),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',FITNESS_NONINTERACTIVE='1')
    password=secrets.token_urlsafe(24);other_password=secrets.token_urlsafe(24);started=False;pid=None;report=None
    normal_db=ROOT/'data/app.sqlite3';normal_before=(normal_db.stat().st_size,normal_db.stat().st_mtime_ns) if normal_db.exists() else None
    def entry(*args):
        completed=subprocess.run(['cmd.exe','/d','/c',str(ROOT/'start.cmd'),*args],cwd=ROOT,env=env,input=b'',capture_output=True,timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW)
        def decode(value):
            try:return value.decode('utf-8')
            except UnicodeDecodeError:return value.decode('gb18030',errors='replace')
        if completed.returncode:raise RuntimeError('正式入口失败：'+decode(completed.stderr)[-600:])
        for line in reversed(decode(completed.stdout).splitlines()):
            if line.startswith('{'):return json.loads(line)
        raise RuntimeError('正式入口未返回状态JSON')
    def stopped():
        nonlocal started
        result=entry('--stop');assert result['stopped'];started=False
        # 只等待本测试取得的PID，不发信号或结束其他进程。
        if pid:
            cmd=f"Wait-Process -Id {pid} -Timeout 10 -ErrorAction SilentlyContinue; if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 1 }}"
            p=subprocess.run(['powershell.exe','-NoProfile','-Command',cmd],cwd=ROOT,env=env,capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
            if p.returncode:raise RuntimeError('本测试服务进程尚未退出，保留目录待核查')
    def prepare_input(client):
        source=ROOT/'data/datasets/development-movements/barbell-row-dvids-639914-side.mp4';boundary='fitness-'+secrets.token_hex(16)
        payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="delivery-row.mp4"\r\nContent-Type: video/mp4\r\n\r\n').encode()+source.read_bytes()+f'\r\n--{boundary}--\r\n'.encode()
        code,result=client.request('/api/v1/inputs','POST',raw=payload,headers={'Content-Type':'multipart/form-data; boundary='+boundary});assert code==202
        iid=result['data']['input_id'];deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            data=client.api('/api/v1/inputs/'+iid)
            if data['status']=='ready':return iid
            if data['status']!='validating':raise RuntimeError('正式视频输入未通过校验')
            time.sleep(.1)
        raise TimeoutError('视频校验尚未确认')
    def start_job(client):
        iid=prepare_input(client);action=client.api('/api/v1/actions/barbell_bent_over_row')
        session=client.api('/api/v1/sessions','POST',{'action_id':action['action_id'],'capability_version':action['capability_version'],'source':'video','mode':'free','target':None,'input_id':iid})
        job=client.api('/api/v1/jobs/video','POST',{'session_id':session['session_id'],'expected_version':session['state_version']});return job['job_id'],session['session_id'],iid
    try:
        db=Database(db_path);initialize_admin(db,'Delivery_Admin',password);db.dispose()
        first=entry('--no-browser','--port','0');started=True;pid=first['pid'];origin=f"http://127.0.0.1:{first['port']}"
        assert not first['reused'];assert entry('--status')['running'];u,v=Client(origin),Client(origin)
        reused=entry('--no-browser','--port','0');assert reused['reused'] and reused['pid']==pid
        assert u.request('/')[0]==200
        u.api('/api/v1/auth/login','POST',{'username':'Delivery_Admin','password':password})
        v.api('/api/v1/auth/register','POST',{'username':'delivery_user@qq.com','password':other_password,'password_confirmation':other_password})
        v.api('/api/v1/auth/login','POST',{'username':'delivery_user@qq.com','password':other_password})
        health=u.api('/api/v1/health');jid,sid,iid=start_job(u);print(json.dumps({'phase':'formal_entry_real_video_started','pid':pid}),flush=True)
        deadline=time.monotonic()+120;last_progress=-1
        while time.monotonic()<deadline:
            job=u.api('/api/v1/jobs/'+jid)
            if job['stopped']:break
            progress=job['processed_frames']//200
            if progress!=last_progress:print(json.dumps({'phase':'processing','frames':job['processed_frames']}),flush=True);last_progress=progress
            time.sleep(.3)
        else:raise TimeoutError('正式视频任务仍在执行，未判定完成')
        assert job['status']=='completed' and job['save_state']=='clean' and job['processed_frames']==855,job['status']
        record=u.api('/api/v1/records/'+sid);assert record['source']=='video' and record['total_count']==3
        assert u.request('/records/'+sid)[0]==200;assert v.request('/api/v1/records/'+sid)[0]==404
        assert v.request('/api/v1/jobs/'+jid)[0]==404;assert v.api('/api/v1/records')['total']==0
        assert u.api('/api/v1/records')['total']==1;assert u.api('/api/v1/inputs/'+iid)['status']=='cleaned'
        interrupted_jid,interrupted_sid,_=start_job(u)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            partial=u.api('/api/v1/jobs/'+interrupted_jid)
            if partial['status']=='running' and partial['processed_frames']>=5:break
            if partial['stopped']:raise RuntimeError('中断场景尚未建立即已停止')
            time.sleep(.1)
        else:raise TimeoutError('未确认第二任务开始处理')
        stopped();assert not entry('--status')['running']
        again=entry('--no-browser','--port','0');started=True;pid=again['pid'];assert not again['reused']
        u=Client(f"http://127.0.0.1:{again['port']}");u.api('/api/v1/auth/login','POST',{'username':'Delivery_Admin','password':password})
        restored=u.api('/api/v1/records/'+sid);interrupted=u.api('/api/v1/jobs/'+interrupted_jid)
        assert restored['total_count']==3 and restored['status']=='completed';assert interrupted['status']=='interrupted' and interrupted['stopped']
        assert interrupted['processed_frames'] is None;assert u.api('/api/v1/jobs')['jobs']==[]
        after=u.api('/api/v1/records/'+interrupted_sid);assert after['status']=='interrupted'
        stopped();assert not entry('--status')['running']
        normal_after=(normal_db.stat().st_size,normal_db.stat().st_mtime_ns) if normal_db.exists() else None;assert normal_after==normal_before
        report={'checks':'passed','app_identity':identity,'environment':environment,'source_sha256':hashes,'scope':'start.cmd production configuration with isolated database; real video/save/ownership/stop/restart',
            'entry':'start.cmd','production_configuration':True,'isolated_database':True,'formal_database_stat_unchanged':True,
            'real_video':{'action_id':record['action_id'],'count':record['total_count'],'frames':job['processed_frames'],'elapsed_ms':job['processing_elapsed_ms'],'source':'video'},
            'cross_account_read_status':404,'restart_preserved_count':restored['total_count'],'stopped_job_status':interrupted['status'],'no_job_resumed':True,
            'camera_hardware_tested':False,'audio_playback_tested':False,'independent_accuracy_truth':False,
            'prior_cli_admin_and_schema_evidence':'steps 02/03 business logic reused; current start/stop/restart entry repaired and measured here',
            'browser_evidence_reused':['data/verification/step-18/demo-analysis-ui.json','data/verification/step-18/showcase-ui.json','data/verification/step-18/voice-missing-ui.json']}
        print(json.dumps({'phase':'formal_entry_checks_passed','count':3,'interrupted_job_after_restart':interrupted['status']}),flush=True)
    finally:
        if not started and (runtime/'instance.json').exists():
            pending=json.loads((runtime/'instance.json').read_text(encoding='utf-8'))
            if entry('--status')['running']:started=True;pid=pending['pid']
            else:raise RuntimeError('测试实例状态未确认，保留专属目录供核查')
        if started:stopped()
        # 只移除此脚本新建的专属目录；不清共用.tmp、数据集或其他运行实例。
        resolved=TEMP.resolve();resolved.relative_to(ROOT/'.tmp/tests')
        if resolved!=TEMP.absolute() or any(p.is_symlink() for p in resolved.rglob('*')):raise RuntimeError('测试目录存在重定向，停止清理')
        shutil.rmtree(resolved);assert not TEMP.exists()
    if report:
        report['temporary_cleanup_verified']=True;OUT.parent.mkdir(exist_ok=True);OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
