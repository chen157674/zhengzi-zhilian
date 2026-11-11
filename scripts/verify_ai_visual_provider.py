"""显式执行真实DeepSeek图像复核；只发送合成／登记公有领域／CC BY帧，不上传私人视频。"""
import argparse
import base64
from io import BytesIO
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
sys.path.insert(0,str(ROOT/'tests'))
from PIL import Image,ImageDraw
from fitness_app.database import Database
from fitness_app.ai_settings import AISettingsStore
from fitness_app.ai_secrets import SecretVault
from fitness_app.ai_context import PROMPT_VERSION
from fitness_app import ai_jobs as ai_jobs_module
from fitness_app.deepseek_client import DeepSeekClient
from fitness_app.research_extract import atomic_json
from fitness_app import db_schema as s
from test_ai_jobs import AIJobsTests

REPORT=ROOT/'data/verification/round3/ai-visual-provider-review.json'


def system_key(database):
    path=project_path(database)
    if not path.is_file():raise ValueError('指定的原配置库不存在')
    db=Database(path)
    try:
        db.inspect_identity()
        store=AISettingsStore(SimpleNamespace(db=db),SecretVault(path.parent/'private/ai-master.key'))
        with db.transaction(False) as connection:
            policy=store.policy(connection)
            if not policy['enabled'] or policy['budget_micro_cny']-policy['spent_micro_cny']-policy['reserved_micro_cny']<200000:
                raise ValueError('原系统AI预算不足，未发送收费请求')
            if not policy['key_ciphertext']:raise ValueError('原系统密钥尚未配置')
            return store.vault.open('system',policy['key_ciphertext'])
    finally:db.dispose()


def diagram(lean,stamp):
    image=Image.new('RGB',(512,512),'#e6e3dd');draw=ImageDraw.Draw(image)
    for x in (90,420):draw.line((x,40,x,455),fill='#777777',width=10)
    draw.line((90,55,420,55),fill='#777777',width=10)
    draw.rectangle((210,350,302,375),fill='#555555');draw.line((256,375,256,470),fill='#555555',width=12)
    cx=256+lean;shoulder=235;hip=350
    draw.polygon([(cx-48,shoulder),(cx+48,shoulder),(286,hip),(226,hip)],fill='#222c39')
    draw.ellipse((cx-26,155,cx+26,211),fill='#b88562');draw.rectangle((cx-11,202,cx+11,238),fill='#b88562')
    for side in (-1,1):
        draw.line([(cx+side*45,shoulder+5),(cx+side*70,170),(cx+side*85,105)],fill='#b88562',width=17)
        draw.ellipse((cx+side*85-9,96,cx+side*85+9,114),fill='#b88562')
        draw.line([(256+side*26,345),(256+side*60,400),(256+side*66,467)],fill='#535f71',width=23)
        draw.ellipse((256+side*66-18,456,256+side*66+21,479),fill='#222222')
    draw.line((cx-100,103,cx+100,103),fill='#444444',width=8)
    memory=BytesIO();image.save(memory,format='JPEG',quality=82)
    return {'data_url':'data:image/jpeg;base64,'+base64.b64encode(memory.getvalue()).decode(),'source_ms':stamp}


def public_samples():
    import av
    from fitness_app.presentation_assets import demo_resource
    from fitness_app.media import probe_video
    source=demo_resource('barbell-row')
    if not source or not source['license'].startswith('PUBLIC DOMAIN') or source.get('source_url')!='https://www.dvidshub.net/video/639914/barbell-bent-over-row':
        raise ValueError('登记的公有领域来源不符，不发送图片')
    path=project_path(source['video']['path']);info=probe_video(path);frames=[]
    track=json.loads(project_path(source['track']['path']).read_text(encoding='utf-8'))
    if track['source_sha256']!=source['video']['sha256']:raise ValueError('原片与动作区间身份不一致')
    # 直接使用生产前端的选择器，避免只靠平均取三帧而落在准备／放回阶段。
    script="import {readFileSync} from 'node:fs';import {reviewSampleTimes} from './fitness_app/static/ai_media_selection.js';const v=JSON.parse(readFileSync(0,'utf8'));console.log(JSON.stringify(reviewSampleTimes(v.record,0,v.duration)));"
    result=subprocess.run([r'E:\01_SoftWare\01_Engineering\nodejs\node.exe','--input-type=module','-e',script],
        input=json.dumps({'record':{'events':track['events']},'duration':info['duration_ms']}),
        capture_output=True,text=True,check=True,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
    stamps=json.loads(result.stdout)
    if len(stamps)!=6 or any(not 0<=t<info['duration_ms'] for t in stamps):raise ValueError('未取得有效动作区间')
    with av.open(str(path)) as container:
        stream=container.streams.video[0]
        for stamp in stamps:
            container.seek(int(stamp/1000/float(stream.time_base)),stream=stream)
            frame=next(f for f in container.decode(stream) if float(f.time)*1000>=stamp)
            image=frame.to_image();image.thumbnail((640,640));memory=BytesIO();image.save(memory,format='JPEG',quality=82)
            frames.append({'data_url':'data:image/jpeg;base64,'+base64.b64encode(memory.getvalue()).decode(),
                           'source_ms':round(float(frame.time)*1000)})
    return frames,{k:source[k] for k in ('id','credit','license','source_url','non_endorsement')}|{
        'source_sha256':source['video']['sha256'],'sampling_policy':'production_selector_confirmed_cycle_six_original_detail',
        'requested_frame_ms':stamps}


def licensed_back_samples(label):
    """公开来源标签只写入本地复核元数据，不出现在给模型的问题或图片名称中。"""
    from scripts.train_visual_posture import cases,full_rep_frames
    manifest=json.loads((ROOT/'data/datasets/manifest.json').read_text(encoding='utf-8'))
    source=next(v for v in manifest['sources'] if v['id']=='w5prmmxyt9')
    if source['doi']!='10.17632/w5prmmxyt9.1' or source['licence']['short_name']!='CC BY 4.0':
        raise ValueError('公开硬拉数据许可或来源不符')
    if label not in {'GB','PB'}:raise ValueError('来源条件不符')
    case=next(v for v in cases() if v['source_subject']=='S06' and v['source_label']==label)
    images,times,metadata=full_rep_frames(case);frames=[]
    for index in (0,1,3,4,6,7):
        image=Image.fromarray(images[index]);image.thumbnail((640,640));memory=BytesIO()
        image.save(memory,format='JPEG',quality=82)
        if len(memory.getvalue())>200*1024:raise ValueError('公开帧超过应用输入边界')
        frames.append({'data_url':'data:image/jpeg;base64,'+base64.b64encode(memory.getvalue()).decode(),
            'source_ms':times[index]})
    return frames,{'id':case['id'],'credit':source['attribution'],'license':source['licence']['short_name'],
        'source_url':source['url'],'source_sha256':case['source_sha256'],'source_condition':label,
        'subject':'S06','source_range_ms':[times[0],times[-1]],
        'sampling_policy':'six_actual_frames_across_whole_original_rep_no_label_in_question',
        'requested_frame_ms':[v['source_ms'] for v in frames],'frame_truth_available':False,
        'dataset_selection':'development_only_not_reserved_test'}


def verify(database,diagnose=False,public_video=False,licensed_back_pair=False,structured_back_pair=False):
    if REPORT.exists() and not (diagnose or public_video or licensed_back_pair or structured_back_pair):raise ValueError('真实调用报告已存在，先核对原结果，不自动重复消费')
    previous=json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else None
    if diagnose and (not previous or previous['cases'][-1].get('error_code')!='AI_EVIDENCE_INVALID'):
        raise ValueError('诊断仅用于已经确认终止的输出校验失败')
    if public_video and previous and (previous['cases'][-1]['status'] in ('queued','running','uncertain') or any(v['name']=='public_barbell_row_six_frames_scene' for v in previous['cases'])):
        raise ValueError('先核对原调用，公有领域视频不重复消费')
    if (licensed_back_pair or structured_back_pair) and previous and previous['cases'][-1]['status'] in ('queued','running','uncertain'):
        raise ValueError('公开硬拉检查仍有未决结果，不发送新请求')
    back_question=('视频里这位训练者在做什么？背部有没有明显姿态问题？只根据所附连续画面描述，'
        '无法确定时说清楚。素材署名：Ivan Conanta／Gloria Virginia，MyDeadlift。')
    cases=[('visible_side_lean',(-55,-65,-58),'这个视频人物有问题嘛？所附是合成的坐姿下拉示意画面，请检查可见姿态。'),
           ('upright_reference',(0,3,-2),'这个视频的姿势有明显侧倾吗？所附是合成的坐姿下拉示意画面。'),
           ('public_barbell_row_six_frames_scene',None,'视频里面的人正在做什么？上衣是什么颜色？请直接描述看见的内容。'),
           ('public_mydeadlift_gb_back_review','licensed:GB',back_question),
           ('public_mydeadlift_pb_back_review','licensed:PB',back_question)]
    if structured_back_pair:
        attempted={v['name'] for v in (previous or {}).get('cases',[])}
        cases=[(name.replace('back_review','structured_review'),leans,question) for name,leans,question in cases[-2:]
            if name.replace('back_review','structured_review') not in attempted]
    elif licensed_back_pair:
        attempted={v['name'] for v in (previous or {}).get('cases',[])}
        cases=[v for v in cases[-2:] if v[0] not in attempted]
    elif public_video:cases=cases[2:3]
    elif diagnose:cases=[v for v in cases if v[0]==previous['cases'][-1]['name']]
    else:cases=cases[:2]
    if not cases:raise ValueError('没有匹配的诊断输入，不发送收费请求')
    key=system_key(database);fixture=AIJobsTests();fixture.setUp()
    document={'schema_version':1,'prompt_version':PROMPT_VERSION,'provider':'DeepSeek official',
        'model':'deepseek-flash','scope':'real_paid_multimodal_application_path_bounded_inputs_not_accuracy_benchmark',
        'private_media_uploaded':False,'production_database_modified':False,'cases':previous['cases'] if previous else []}
    for case in document['cases']:
        case.setdefault('prompt_version',previous.get('prompt_version',PROMPT_VERSION))
        if case['name']=='public_barbell_row_scene' and 'semantic_review' not in case:
            case['semantic_review']={'expected_action':'barbell_bent_over_row','mentions_row':False,
                'sampling_issue':'均匀抽样落在准备和放回阶段，未展示完整划船动作',
                'not_an_independent_accuracy_benchmark':True}
    validation=ai_jobs_module.validate_answer;diagnostics={}
    def observe_validation(raw,bundle):
        try:return validation(raw,bundle)
        except ValueError as error:
            diagnostics.update(reason=str(error),candidate=raw)
            raise
    ai_jobs_module.validate_answer=observe_validation
    try:
        fx=fixture.fx;settings=fx.app.extensions['ai_settings']
        settings.write(fx.user,fx.auth,{'expected_version':1,'personal_key':key},s.new_id());key=None
        fx.app.extensions['ai_provider_factory']=DeepSeekClient
        for name,leans,question in cases:
            diagnostics.clear()
            response=fixture.post('/api/v1/ai/conversations',{'context_kind':'general','history_scope':'none'})
            if response.status_code!=201:raise ValueError('测试对话建立失败')
            conversation=response.json['data'];source=None
            if isinstance(leans,str) and leans.startswith('licensed:'):frames,source=licensed_back_samples(leans.split(':')[1])
            elif leans is None:frames,source=public_samples()
            else:frames=[diagram(value,(index+1)*1000) for index,value in enumerate(leans)]
            response=fixture.post('/api/v1/ai/conversations/'+conversation['conversation_id']+'/messages',{
                'text':question,'consent':True,'include_current_frame':True,'frames':frames,
                'expected_context_revision':conversation['context_revision']})
            frames=None
            if response.status_code!=202:raise ValueError('真实请求未进入AI任务：'+str(response.json.get('error',{}).get('code')))
            rid=response.json['data']['request_id'];deadline=time.monotonic()+65
            while True:
                row=fixture.manager.snapshot(fx.user,fx.auth,rid)
                if row['status'] in ('succeeded','failed','cancelled','uncertain'):break
                if time.monotonic()>=deadline:raise RuntimeError('请求仍在执行，不能将观察超时当成失败并重发')
                time.sleep(.1)
            case={k:row.get(k) for k in ('status','model','usage','estimated_micro_cny','error_code')}
            case.update(name=name,summary=(row.get('result') or {}).get('summary'),
                facts=(row.get('result') or {}).get('facts'),request_id=rid,prompt_version=PROMPT_VERSION)
            if (row.get('result') or {}).get('visual_review') is not None:
                case['visual_review']=row['result']['visual_review']
            case['input_origin']=('cc_by_video' if source.get('license')=='CC BY 4.0' else 'public_domain_video') if source else 'synthetic_diagram'
            if source:case['public_source']=source
            if name=='public_barbell_row_six_frames_scene':
                case['semantic_review']={'expected_action':'barbell_bent_over_row',
                    'mentions_row':bool(re.search(r'划船|bent[\s-]*over[\s-]*row',case['summary'] or '',re.I)),
                    'not_an_independent_accuracy_benchmark':True}
            if name.startswith('public_mydeadlift_'):
                case['semantic_review']={'source_condition':source['source_condition'],
                    'question_contains_source_condition':False,'frame_ground_truth_available':False,
                    'content_review':'pending_not_implied_by_application_success',
                    'not_an_independent_accuracy_benchmark':True}
            if row.get('error_code')=='AI_EVIDENCE_INVALID':
                case['validation_diagnostics']=dict(diagnostics)
            document['cases'].append(case);atomic_json(REPORT,document)
            print(json.dumps({k:case[k] for k in ('name','status','summary','estimated_micro_cny','error_code')},ensure_ascii=False),flush=True)
            if row['status']!='succeeded':raise RuntimeError('真实调用未成功；已保留结果，不自动重试')
            fixture.manager.close(fx.user,fx.auth,conversation['conversation_id'])
    finally:
        temporary=fixture.fx.db.path.parent
        fixture.tearDown()
        ai_jobs_module.validate_answer=validation
        document['test_database_and_key_copy_cleaned']=not temporary.exists()
        document['estimated_total_micro_cny']=sum(v['estimated_micro_cny'] or 0 for v in document['cases'])
        document['total_calls']=len(document['cases'])
        document['application_accepted_calls']=sum(v['status']=='succeeded' for v in document['cases'])
        document['failed_calls']=sum(v['status']=='failed' for v in document['cases'])
        if document['cases']:atomic_json(REPORT,document)
        key=None


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--database',required=True)
    parser.add_argument('--run-paid',action='store_true')
    group=parser.add_mutually_exclusive_group()
    group.add_argument('--diagnose',action='store_true',help='为已确认终止的校验失败采集诊断，不用于超时重发')
    group.add_argument('--public-video',action='store_true',help='只验证登记公有领域视频的一组抽样帧')
    group.add_argument('--licensed-back-pair',action='store_true',help='只验证公开CC BY开发分区的同人GB／PB原片')
    group.add_argument('--structured-back-pair',action='store_true',help='以当前结构化协议验证同一公开GB／PB开发原片，不重发已尝试请求')
    args=parser.parse_args()
    if not args.run_paid:parser.error('真实验证会消费API额度，需要显式--run-paid')
    verify(args.database,args.diagnose,args.public_video,args.licensed_back_pair,args.structured_back_pair)
