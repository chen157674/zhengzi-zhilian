"""对参赛副本执行真实本机HTTP、视频模型及记录流程检查。"""
from pathlib import Path
import json,os,subprocess,sys,socket,time,urllib.request,http.cookiejar,secrets
ROOT=Path(__file__).resolve().parents[1];BUILD=ROOT/'.tmp/opc-completion';APP=BUILD/'作品工程'
FFMPEG=Path(r'E:\07_Projects\07_MonetizationTests\02_KnowledgeVideos\open-source\huashu-art-motion\.venv\Scripts\ffmpeg.exe')
PYTHON=ROOT/'.venv/Scripts/python.exe'
TMP=APP/'.tmp/delivery-check';TMP.mkdir(parents=True,exist_ok=True)
video=TMP/'input.mp4'
source=APP/'data/datasets/user-demos/user-1689.mp4'
subprocess.run([str(FFMPEG),'-hide_banner','-loglevel','error','-y','-ss','3','-i',str(source),'-t','5','-an','-c:v','libx264','-preset','ultrafast','-crf','22',str(video)],check=True)
with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
env=os.environ.copy();env.update(TEMP=str(TMP),TMP=str(TMP),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',FITNESS_DATABASE_PATH=str(TMP/'app.sqlite3'),FITNESS_RUNTIME_DIR='.tmp/delivery-check/runtime',FITNESS_BODY_LEAN='1',FITNESS_MOVEMENT_GUIDANCE='1',FITNESS_PRETRAINED_CORRECTION='0')
# 所有运行数据限于交付副本，不复制开发账号或调用付费AI。
base=f'http://127.0.0.1:{port}';opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
def get(path,timeout=20):
    with opener.open(base+path,timeout=timeout) as response:return json.load(response)['data']
def post(path,data,timeout=30):
    token=get('/api/v1/auth/csrf')['csrf_token']
    req=urllib.request.Request(base+path,json.dumps(data).encode(),headers={'Content-Type':'application/json','X-CSRFToken':token,'Idempotency-Key':__import__('uuid').uuid4().hex})
    with opener.open(req,timeout=timeout) as response:return json.load(response)['data']
report={'scope':'本机交付副本、独立空库、已有Python依赖、实际视频推理；AI配置页面核验，无付费生成请求'}
log=(BUILD/'delivery-runtime.log').open('wb')
proc=subprocess.Popen([str(PYTHON),'-B','-m','fitness_app','serve','--port',str(port)],cwd=APP,env=env,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
try:
    for _ in range(120):
        if proc.poll() is not None:raise RuntimeError('交付服务退出')
        try:
            health=get('/api/v1/health',1);break
        except OSError:time.sleep(.5)
    else:raise RuntimeError('启动超时')
    assert health['ready'];report['health']='passed';print('独立副本启动通过',flush=True)
    for route in ['/login','/register']:
        with opener.open(base+route) as response:assert response.status==200
    password=secrets.token_urlsafe(24)
    account='package-check-'+__import__('uuid').uuid4().hex[:10]+'@163.com'
    post('/api/v1/auth/register' if health['initialized'] else '/api/v1/auth/setup',
         {'email':account,'password':password,'password_confirmation':password})
    post('/api/v1/auth/login',{'email':account,'password':password})
    report['registration_login']='passed'
    for route in ['/','/video','/realtime','/coach','/records','/account/training-profile']:
        with opener.open(base+route) as response:assert response.status==200
    report['pages']='passed';print('注册登录和核心页面通过',flush=True)
    token=get('/api/v1/auth/csrf')['csrf_token'];boundary='FitnessDeliveryBoundary';payload=(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="input.mp4"\r\nContent-Type: video/mp4\r\n\r\n'.encode()+video.read_bytes()+f'\r\n--{boundary}--\r\n'.encode())
    req=urllib.request.Request(base+'/api/v1/inputs',payload,headers={'Content-Type':f'multipart/form-data; boundary={boundary}','X-CSRFToken':token,'Idempotency-Key':__import__('uuid').uuid4().hex})
    with opener.open(req,timeout=30) as response:uploaded=json.load(response)['data']
    input_id=uploaded.get('input_id') or uploaded.get('id')
    if not input_id:raise RuntimeError('上传响应缺少输入标识')
    for _ in range(100):
        state=get('/api/v1/inputs/'+input_id)
        if state['status']=='ready':break
        if state['status']=='rejected':raise RuntimeError('视频输入校验失败')
        time.sleep(.3)
    else:raise RuntimeError('输入准备超时')
    started=time.monotonic();analysis=post('/api/v1/video-analyses',{'input_id':input_id,'mode':'auto','coaching_view':'auto','save_skeleton':True})
    analysis_id=analysis.get('id') or analysis.get('analysis_id')
    if not analysis_id:raise RuntimeError('分析响应缺少标识：'+str(list(analysis)))
    previous=None
    for _ in range(600):
        result=get('/api/v1/video-analyses/'+analysis_id,30)
        status=result['status'];phase=result.get('phase')
        if (status,phase)!=previous:print('视频任务',status,phase,flush=True);previous=(status,phase)
        if status in ['completed','failed','cancelled','interrupted']:break
        time.sleep(1)
    else:raise RuntimeError('真实自动分析超时')
    if result['status']!='completed':raise RuntimeError('真实自动分析失败：'+json.dumps({k:v for k,v in result.items() if k in ['status','error_code','phase']},ensure_ascii=False))
    segments=result.get('segments',[])
    report['real_video_analysis']={'status':'passed','seconds':round(time.monotonic()-started,2),'input_seconds':5,'segments':len(segments),'actions':[s.get('action_id') for s in segments]}
    report['records_page']='passed';report['port']=port
    target=ROOT/'OPC专项赛/补充成果附件/E13_完整运行交付核验.json';target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(report,ensure_ascii=False),flush=True)
finally:
    if proc.poll() is None:proc.terminate()
    proc.wait(timeout=25);log.close()
