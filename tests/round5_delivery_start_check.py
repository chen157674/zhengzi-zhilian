"""验证当前源码交付实际正常启动；仅项目隔离目录，原用户库不操作。"""
import os,json,subprocess,sys,shutil,time,urllib.request,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TASK=ROOT/'第五轮优化'
WORK=TASK/'.verification/delivery-start'
if WORK.exists():raise RuntimeError('本任务验证目录已存在，先核对归属，不覆盖')
assert WORK.resolve().is_relative_to(TASK.resolve())
WORK.mkdir(parents=True)
env={**os.environ,'FITNESS_DATABASE_PATH':str(WORK/'app.sqlite3'),'FITNESS_RUNTIME_DIR':str(WORK/'runtime'),'PYTHONDONTWRITEBYTECODE':'1','TEMP':str(WORK),'TMP':str(WORK),'FITNESS_NONINTERACTIVE':'1'}
python=ROOT/'.venv/Scripts/python.exe'
started=False
try:
    seed="from fitness_app import create_app;from fitness_app.auth import initialize_admin;import os;app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});initialize_admin(app.extensions['database'],'Delivery_Admin','delivery-isolated-password');app.extensions['database'].dispose()"
    prepared=subprocess.run([str(python),'-B','-c',seed],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
    assert prepared.returncode==0,prepared.stderr[-1000:]
    # 经实际start.ps1入口启动，依赖仍使用当前已有项目环境，不打无需求安装包。
    command=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts/start.ps1'),'--no-browser','--port','0']
    launched=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=40,creationflags=subprocess.CREATE_NO_WINDOW)
    if launched.returncode:raise RuntimeError('实际启动入口失败：'+launched.stderr[-1000:])
    lines=[line for line in launched.stdout.splitlines() if line.startswith('{')];info=json.loads(lines[-1]);started=True
    with urllib.request.urlopen('http://127.0.0.1:'+str(info['port'])+'/api/v1/health') as response:health=json.load(response)['data']
    assert health['project_id']=='fitness-assistant' and health['schema_version']==13
    # urllib cookie jar proves actual production identity/CSRF, no test-client bypass.
    import http.cookiejar
    opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    origin='http://127.0.0.1:'+str(info['port'])
    token=json.load(opener.open(origin+'/api/v1/auth/csrf'))['data']['csrf_token']
    login=urllib.request.Request(origin+'/api/v1/auth/login',data=json.dumps({'username':'Delivery_Admin','password':'delivery-isolated-password'}).encode(),headers={'Content-Type':'application/json','X-CSRFToken':token},method='POST')
    assert opener.open(login).status==200
    pages=['/','/training/plan','/training/plans/new','/training/review','/coach/knowledge','/coach/nutrition','/coach/tasks','/records?view=list','/settings']
    for route in pages:assert opener.open(origin+route).status==200,route
    for route in ['/api/v1/training/plans/current','/api/v1/training/today','/api/v1/training/progress','/api/v1/training/reviews/week','/api/v1/knowledge/catalog']:
        data=opener.open(origin+route);assert data.status==200 and data.headers['Cache-Control']=='no-store'
    # 正式启动入口下再走真实计划写入/启用/执行/组保存，不只验证路由可打开。
    import datetime as dt,uuid
    def write(path,payload,method='POST'):
        token=json.load(opener.open(origin+'/api/v1/auth/csrf'))['data']['csrf_token']
        request=urllib.request.Request(origin+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','X-CSRFToken':token,'Idempotency-Key':str(uuid.uuid4())},method=method)
        return json.load(opener.open(request))['data']
    today=dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date();monday=today-dt.timedelta(days=today.weekday())
    draft={'title':'正式入口交付核对','goal':'general_fitness','start_date':monday.isoformat(),'weeks':1,'timezone':'Asia/Shanghai','constraints':{'experience':'beginner','training_weekdays':[today.isoweekday()],'minutes_per_session':30,'equipment':['bodyweight']},'sessions':[{'client_id':'day','date':today.isoformat(),'day_kind':'training','title':'启动核对','estimated_minutes':30,'items':[{'client_id':'item','action_id':'bodyweight_squat','variant':None,'target_sets':1,'target_reps':1,'target_duration_ms':None,'rest_seconds':0,'load':None,'focus_task_id':None}]}]}
    plan=write('/api/v1/training/plan-drafts',draft)
    plan=write('/api/v1/training/plans/'+plan['id']+'/activate',{'expected_version':plan['state_version'],'confirmed':True})
    session=next(x for x in plan['sessions'] if x['scheduled_date']==today.isoformat())
    workout=write('/api/v1/training/workouts',{'plan_session_id':session['id'],'session_expected_version':session['schedule_version']})
    workout=write('/api/v1/training/workouts/'+workout['id']+'/commands',{'expected_version':workout['state_version'],'type':'start'})
    workout=write('/api/v1/training/workouts/'+workout['id']+'/items/'+workout['items'][0]['item_id']+'/sets/1',{'expected_version':workout['state_version'],'actual_reps':1,'status':'done','source':'manual'},'PUT')
    workout=write('/api/v1/training/workouts/'+workout['id']+'/commands',{'expected_version':workout['state_version'],'type':'complete','confirmed':True})
    assert workout['status']=='completed'
    facts=json.load(opener.open(origin+'/api/v1/training/reviews/week'))['data'];assert facts['plan_progress']['completed_sessions']==1
    # 指纹覆盖实际交付代码/模板/静态和启动入口；模型清单另列，避免复制大型模型。
    source_files=sorted([p for p in (ROOT/'fitness_app').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix in {'.py','.js','.css','.html'}]+[ROOT/'scripts/start.ps1',ROOT/'scripts/environment.ps1'])
    source_hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files}
    resource_names=['data/knowledge/index/manifest.json','data/models/semantic-action-classifier.json','data/presentation-assets.json']
    resource_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in resource_names}
    fingerprint=hashlib.sha256(json.dumps(source_hashes,sort_keys=True).encode()).hexdigest()
    report={'scope':'actual_current_source_delivery_start','entry':'scripts/start.ps1 --no-browser --port 0','schema_version':13,'authenticated_pages':pages,'private_apis_no_store':True,'normal_runtime_plan_execution_to_review':True,'new_isolated_database':True,'original_databases_modified':False,'paid_api_calls':0,'model_inference_calls':0,'source_fingerprint':fingerprint,'source_files':source_hashes,'resource_manifests':resource_hashes,'result':'passed'}
    output=(ROOT/os.environ.get('FITNESS_START_EVIDENCE','第五轮优化/实现核对/源码交付启动核对.json')).resolve();assert output.is_relative_to((TASK/'实现核对').resolve()) and output.suffix=='.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'source_files','resource_manifests'}},ensure_ascii=False))
finally:
    if started:
        stopped=subprocess.run([str(python),'-B','-m','fitness_app','--stop'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=20,creationflags=subprocess.CREATE_NO_WINDOW)
        if stopped.returncode:raise RuntimeError('本任务服务未确认停止，保留目录待核对')
    actual=WORK.resolve();assert actual.is_relative_to(TASK.resolve()) and actual==WORK
    shutil.rmtree(actual)
    assert not WORK.exists()
