"""只下载发布页明确允许CC复用的视频；不读取浏览器登录、不绕过访问限制。"""
import sys,json,hashlib,argparse,time
from datetime import date
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json

SOURCES=(
    ('i14hrj7l7CI','cable_triceps_pushdown'),
    ('qmFQTDYwpsg','dumbbell_front_raise'),
    ('IXeRFh9nUwk','dumbbell_lateral_raise'),
    ('7bLivsAhDFY','cable_face_pull'),
    ('Hy5L3-QJETc','hammer_curl'),
    ('d1QEddtoOq0','bent_over_dumbbell_reverse_fly'),
    ('dC7jhEk-29A','reverse_pec_deck_fly'),
    ('NBkMIWWkIKQ','cable_y_raise'),
    ('3frdI6LLJ3Y','cable_y_raise'),
    ('U5uW6jaOiLg','dumbbell_chest_fly'),
    ('SQ1WOlq5UqM','dumbbell_chest_fly'),
    ('hrh0K1Oo7Yc','cable_chest_fly'),
    ('4Y8QgiT2-OA','cable_chest_fly'),
    ('gx0RWT7WbmA','assisted_pull_up'),
    ('I01erzk7FDk','assisted_pull_up'),
    ('7BkgqzC6WsM','seated_cable_row'),
    ('4iNlLpIzT9w','seated_cable_row'),
    ('X-iV-cG8cYs','overhead_dumbbell_extension'),
    ('K8o68rDbri0','overhead_dumbbell_extension'),
    ('Y29xKcze8Ik','dumbbell_lateral_raise'),
    ('wnPX6Fwe-Fg','cable_face_pull'),
    ('-buTln7JnFw','cable_triceps_pushdown'),
    ('ipn9Lz3JmSI','reverse_pec_deck_fly'),
    ('LsMLd6Pwwvw','dumbbell_front_raise'),
    ('FNvndC4Ov04','hammer_curl'),
    ('I4LyZpwysGI','bent_over_dumbbell_reverse_fly'),
    ('BqYsKRzc0wM','barbell_bench_press'),
    ('JjyvW9IpBXw','dumbbell_bench_press'),
    ('i1fywF_g-1U','dumbbell_bench_press'),
    ('awEEyL5zGvU','incline_dumbbell_press'),
    ('P8Hx90LdGH4','machine_chest_press'),
    ('H4mVGHaK2f4','pec_deck_fly'),
    ('Dbly77Jgbo8','pec_deck_fly'),
    ('OOe_HrNnQWw','dumbbell_shoulder_press'),
    ('ScAYp-Gik_E','pull_up'),
)
# 之前核查的ETtXO4FW1EU、QENKPHhQVi4未授予CC复用；oDSGrucETfw不可用，不重复请求。


def outcome_exit_status(outcomes):
    if not outcomes:return 2
    return 1 if any(r['status'] in {'failed','rejected_license'} for r in outcomes) else 0


def run(only=None,selected=None,*,directory=None,policies=None,partition_roles=None):
    import yt_dlp
    dest=project_path(directory or 'data/datasets/development-movements');manifest_path=dest/'sources.json';outcomes=[];policies=policies or {}
    if partition_roles is not None:partition_roles={k:set(v) for k,v in partition_roles.items()}
    for vid,action in SOURCES if selected is None else selected:
        if only and vid!=only:continue
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        ident='youtube-'+vid
        previous=next((r for r in manifest['sources'] if r['id']==ident),None)
        if previous:
            try:
                path=project_path(previous['path'])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=previous['sha256'] or previous.get('bytes',path.stat().st_size)!=path.stat().st_size:
                    raise ValueError('已登记原片缺失或摘要/体积改变，不自动覆盖或宣称复用成功')
                outcome={'id':ident,'status':'reused','reused':True,'partition':previous.get('partition')}
            except (OSError,ValueError) as error:outcome={'id':ident,'status':'failed','failed':type(error).__name__,'message':str(error)[:350]}
            outcomes.append(outcome);print(json.dumps(outcome,ensure_ascii=False),flush=True);continue
        existing_files=set(dest.glob(ident+'.*'))
        progress_state={'last':None}
        def progress(value,source_id=ident,state=progress_state):
            now=time.monotonic()
            if value.get('status') not in {'downloading','finished'}:return
            if value['status']!='finished' and state['last'] is not None and now-state['last']<30:return
            state['last']=now
            print(json.dumps({'id':source_id,'phase':'media_transfer_complete' if value['status']=='finished' else 'media_transfer',
                'downloaded_bytes':value.get('downloaded_bytes'),'total_bytes':value.get('total_bytes') or value.get('total_bytes_estimate')}),flush=True)
        options={'cachedir':False,'noplaylist':True,'quiet':True,'noprogress':True,'format':'bestvideo[height<=480]',
                 'outtmpl':{'default':str(dest/(ident+'.%(ext)s'))},
                 'overwrites':False,'max_filesize':80*1024*1024,'socket_timeout':30,'retries':1,'fragment_retries':1,
                 'js_runtimes':{'node':{'path':'E:/01_SoftWare/01_Engineering/nodejs/node.exe'}},
                 'writesubtitles':False,'writeautomaticsub':False,'writethumbnail':False,'progress_hooks':[progress]}
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                info=downloader.extract_info('https://www.youtube.com/watch?v='+vid,download=False)
                license=info.get('license')
                if not isinstance(license,str) or 'Creative Commons Attribution' not in license:
                    outcome={'id':ident,'status':'rejected_license','skipped':'publisher_has_not_granted_cc_reuse'};outcomes.append(outcome);print(json.dumps(outcome),flush=True);continue
                planned=policies.get(vid,{})
                if planned.get('expected_channel_id') and info.get('channel_id')!=planned['expected_channel_id']:raise ValueError('发布者与前瞻分区计划不一致，未下载')
                role=planned.get('prospective_role')
                if role and role not in {'train','validation_supplement','reserved_new_publisher_holdout'}:raise ValueError('未确认的新来源分区角色，未下载')
                if role:
                    from fitness_app.source_partitions import model_partition_roles,validate_prospective_role
                    if not isinstance(info.get('channel_id'),str) or not info['channel_id']:raise ValueError('新来源没有明确主站作者ID，不能登记分区')
                    if partition_roles is None:partition_roles=model_partition_roles()
                    validate_prospective_role('youtube-channel:'+info['channel_id'],role,partition_roles)
                path=project_path(downloader.prepare_filename(info));path.relative_to(dest)
                if existing_files:raise ValueError('未登记的已有视频或中间文件，不覆盖')
                downloader.process_info(info)
                if not path.is_file():raise ValueError('视频未成功下载')
                size=path.stat().st_size
                if size<10000:raise ValueError('视频文件异常')
                row={'id':ident,'action_id':action,'title':info['title'],'author':info.get('uploader'),
                     'source_group':'youtube-channel:'+str(info.get('channel_id') or info.get('uploader_id')),
                     'source_url':'https://www.youtube.com/watch?v='+vid,'path':path.relative_to(ROOT).as_posix(),
                     'copyright_status':license,'primary_license_verified_date':date.today().isoformat(),
                     'terms_url':'https://www.youtube.com/t/creative_commons','license_reference':'https://creativecommons.org/licenses/by/4.0/',
                     'license_version_note':'Current linked CC BY terms; pre-August-2025 uploads may retain CC BY 3.0, preserve original declaration',
                     'format_id':info.get('format_id'),'height':info.get('height'),'duration_seconds':info.get('duration'),
                     'restrictions':'Credit original author/title/source/license, identify changes; no endorsement.',
                     'use_scope':'Local action recognition development only; original audio not downloaded; not a product tutorial',
                     'role':'development_candidate','awaiting_segment_review':True,
                     'ground_truth':'Publisher action title; talking/setup/other examples need temporal review; no independent per-frame error labels',
                     'bytes':size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
                if role:
                    row.update(prospective_role=role,partition='reserved_holdout' if role=='reserved_new_publisher_holdout' else 'validation' if role=='validation_supplement' else 'train',
                        evaluation_only=role!='train',model_inference_allowed_before_freeze=role!='reserved_new_publisher_holdout')
                manifest=json.loads(manifest_path.read_text(encoding='utf-8'));manifest['sources'].append(row)
                atomic_json(manifest_path,manifest)
                if role:partition_roles.setdefault(row['source_group'],set()).add(row['partition'])
                outcome={'id':ident,'status':'downloaded','action':action,'bytes':size};outcomes.append(outcome);print(json.dumps(outcome),flush=True)
        except (yt_dlp.utils.DownloadError,OSError,ValueError) as error:
            pending=[]
            for created in set(dest.glob(ident+'.*'))-existing_files:
                if created.is_file():
                    target=project_path(created)
                    try:target.unlink()
                    except OSError as cleanup_error:pending.append({'path':target.relative_to(ROOT).as_posix(),'error':type(cleanup_error).__name__})
            outcome={'id':ident,'status':'failed','failed':type(error).__name__,'message':str(error)[:350]};outcomes.append(outcome);print(json.dumps(outcome),flush=True)
            if pending:
                outcome['cleanup_pending']=pending
                print(json.dumps({'id':ident,'phase':'cleanup_pending_after_failure','files':pending}),flush=True)
            # 保留失败事实；不切换账号、代理或获取登录凭据重试。
    return outcomes


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--only');parser.add_argument('--selection');parser.add_argument('--start-index',type=int,default=0);args=parser.parse_args()
    selected=json.loads(project_path(args.selection).read_text(encoding='utf-8'))['videos'] if args.selection else None
    chosen=[r for r in selected[args.start_index:] if not r.get('skip_download')] if selected is not None else None
    outcomes=run(args.only,[(r['id'],r['action_id']) for r in chosen] if chosen is not None else None,policies={r['id']:r for r in chosen} if chosen else None)
    print(json.dumps({'completed_sources':len(outcomes),'exit_status':outcome_exit_status(outcomes)},ensure_ascii=False),flush=True)
    raise SystemExit(outcome_exit_status(outcomes))
