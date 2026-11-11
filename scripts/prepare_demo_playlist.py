"""新演示先用正式离线识别/纠错流程检查，再选择发布；不改原片或训练模型。"""
from collections import Counter
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment, project_path
configure_environment()
from fitness_app.catalogue import get_action
from fitness_app.offline_video import scan_video
from fitness_app.video_segmentation import segment_decisions
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.posture_rules import policy_version
from fitness_app.coaching import coaching_observation, coaching_snapshot, committed_issues
from fitness_app.demo_feedback import build_demo_feedback, FEEDBACK_SCHEMA_VERSION
from fitness_app.research_extract import atomic_json
from fitness_app.media import probe_video
from scripts.prepare_user_demos import SOURCE, MANIFEST, convert, poster, digest, asset

NUMBERS = ('1688', '1689', '1690', '1692', '1693', '1694')
DISPLAY_NAMES = {'1692': '器械推胸 · 侧前方', '1693': '器械推胸 · 需调整近景', '1694': '器械推胸 · 需调整连续动作'}
REPORTED_ISSUES = {'1693': ['身体侧倾', '握持不当'], '1694': ['身体侧倾', '握持不当']}
STAGE = project_path('.tmp/demo-playlist')


def make_track(scan, action, info, sha, destination):
    engine = create_analysis_engine(action)
    segments = segment_decisions(scan.decisions, scan.end_ms, scan.unassessable)
    rows, statuses, states = [], Counter(), Counter()
    segment_index = 0
    previous_valid = False
    for index, row in enumerate(scan.rows):
        while segment_index + 1 < len(segments) and row.stamp >= segments[segment_index]['end_ms']:
            segment_index += 1
        valid = segments[segment_index]['action_id'] == action and row.identity_accepted
        if valid and not previous_valid:
            engine = create_analysis_engine(action)
        frame = row.frame(action, index)
        frame['identity_accepted'] = valid
        output = engine.process(frame, {'coaching_view': 'auto', 'stream_epoch': 'demo-playlist'}) if valid else {}
        observation = coaching_observation(output)
        cue = coaching_snapshot({'status': 'running', 'action_id': action, 'supported_items': engine.rules},
            {**frame, 'analysis': observation}, committed_issues(output))
        feedback = build_demo_feedback(frame, output, engine.rules, cue)
        # 这些新输入存在未被现有规则覆盖的表现，未检出不能被写成整体动作正常。
        if feedback['state'] == 'clear':
            feedback['clear_label'] = '已检查项目未见偏差'
        points = frame['framing'].get('landmarks', [])
        values = [[round(p['x'], 6), round(p['y'], 6)] if p.get('valid') and 0 <= p['x'] <= 1 and 0 <= p['y'] <= 1 else None for p in points] if len(points) == 33 and valid else [None] * 33
        for item in observation.get('posture', {}).get('items', []):
            statuses[item['code'] + ':' + item['status']] += 1
        states[feedback['state']] += 1
        rows.append({'t': row.stamp, 'p': values, 'people': row.people, 'feedback': feedback})
        previous_valid = valid
    value = {'schema_version': 1, 'feedback_schema_version': FEEDBACK_SCHEMA_VERSION, 'source_sha256': sha,
        'width': info['width'], 'height': info['height'], 'mode': 'preprocessed_user_source_not_live',
        'action_id': action, 'frames': rows, 'events': [], 'visible_frames': sum(any(p is not None for p in row['p']) for row in rows),
        'accuracy_status': 'no_independent_ground_truth', 'rule_version': policy_version(),
        'feedback_implementation_sha256': digest(ROOT / 'fitness_app/demo_feedback.py'),
        'posture_status_counts': dict(statuses), 'feedback_state_counts': dict(states),
        'classification_source': 'formal_offline_scan_and_segments', 'no_personal_training_records_created': True}
    atomic_json(destination, value)
    return value


def review():
    STAGE.mkdir(exist_ok=False)
    reviews = []
    atomic_json(STAGE / 'review.json', {'owner': 'demo-playlist', 'videos': reviews})
    for number in NUMBERS:
        source = SOURCE / f'IMG_{number}.MOV'
        source_sha = digest(source)
        print(json.dumps({'stage': 'convert', 'source': source.name}), flush=True)
        target = STAGE / f'user-{number}.mp4'
        converted = convert(source, target)
        info = probe_video(target)
        print(json.dumps({'stage': 'recognize', 'source': source.name, 'duration_ms': info['duration_ms']}), flush=True)
        last_progress = [-10000]
        def progress(stamp, model):
            if stamp - last_progress[0] >= 5000:
                print(json.dumps({'source': source.name, 'processed_ms': stamp}), flush=True)
                last_progress[0] = stamp
        scan = scan_video(target, info, progress=progress)
        segments = segment_decisions(scan.decisions, scan.end_ms, scan.unassessable)
        durations = Counter()
        for segment in segments:
            durations[segment['action_id'] or 'unknown'] += segment['end_ms'] - segment['start_ms']
        known = [(duration, action) for action, duration in durations.items() if action != 'unknown']
        action = max(known)[1] if known else None
        record = {'number': number, 'source_name': source.name, 'source_sha256': source_sha, 'source_bytes': source.stat().st_size,
            'video_sha256': digest(target), 'info': info, 'conversion': converted, 'main_action': action,
            'durations_ms': dict(durations), 'decisions': scan.decisions, 'segments': segments, 'pose_calls': scan.pose_calls}
        if action:
            data = make_track(scan, action, info, record['video_sha256'], STAGE / f'user-{number}-track.json')
            record.update(feedback_state_counts=data['feedback_state_counts'], posture_status_counts=data['posture_status_counts'])
            record['issues'] = list({row['feedback']['title'] for row in data['frames'] if row['feedback']['state'] == 'issue'})
            poster(target, STAGE / f'user-{number}-poster.jpg')
        reviews.append(record)
        atomic_json(STAGE / 'review.json', {'owner': 'demo-playlist', 'videos': reviews, 'rule_version': policy_version()})
        print(json.dumps({key: record.get(key) for key in ('number', 'main_action', 'durations_ms', 'feedback_state_counts', 'issues')}, ensure_ascii=False), flush=True)
        scan.clear()


def publish(numbers):
    from PIL import Image
    data = json.loads((STAGE / 'review.json').read_text(encoding='utf-8'))
    if data.get('owner') != 'demo-playlist' or len(numbers) != 3 or len(set(numbers)) != 3:
        raise ValueError('发布应选择3段新演示，与原演示组成4段')
    doc = json.loads(MANIFEST.read_text(encoding='utf-8'))
    for number in numbers:
        item = next(row for row in data['videos'] if row['number'] == number)
        if not item['main_action'] or digest(SOURCE / item['source_name']) != item['source_sha256']:
            raise ValueError('原片改变或未形成动作识别结果')
        demo_id = 'user-' + number
        if demo_id in doc['demos']:
            raise ValueError('演示已登记，停止重复发布')
        paths = {}
        for kind, suffix in [('video', '.mp4'), ('poster', '-poster.jpg'), ('track', '-track.json')]:
            target = project_path(('data/datasets/user-demos/' if kind == 'video' else 'fitness_app/static/demo/') + demo_id + suffix)
            if target.exists():
                raise ValueError('目标已存在，拒绝覆盖未知文件')
            source = STAGE / (demo_id + suffix)
            if kind == 'video' and digest(source) != item['video_sha256']:
                raise ValueError('复核后的视频内容改变')
            source.replace(target)
            paths[kind] = asset(target)
        with Image.open(ROOT / paths['poster']['path']) as image:
            paths['poster'].update(width=image.width, height=image.height)
        track = json.loads((ROOT / paths['track']['path']).read_text(encoding='utf-8'))
        if number in REPORTED_ISSUES:
            track['user_reported_issues'] = REPORTED_ISSUES[number]
            for row in track['frames']:
                if row['feedback']['state'] == 'clear':
                    row['feedback']['clear_label'] = '你标记的问题尚未检出'
            atomic_json(ROOT / paths['track']['path'],track)
            paths['track'] = asset(ROOT / paths['track']['path'])
        title = get_action(item['main_action'])['name']
        doc['demos'][demo_id] = {'id': demo_id, 'action_id': item['main_action'], 'title': title,
            'display_title': DISPLAY_NAMES.get(number,title),
            'user_reported_issues': REPORTED_ISSUES.get(number,[]),
            'demonstration_intent': 'user_confirmed_adjustment' if number in REPORTED_ISSUES else 'training_sample',
            'source_name': item['source_name'], 'source_sha256': item['source_sha256'], 'source_bytes': item['source_bytes'],
            'source_type': 'user_supplied', 'source_url': None, 'source_label': '用户提供的本机训练视频',
            'credit': '用户提供', 'license': '用户授权用于本项目演示',
            'use_scope': '本项目演示与纠错验证；不加入识别训练集、不上传第三方AI',
            'non_endorsement': '检测结果仅对应已评估项目，不代表整体动作正确。',
            'width': item['info']['width'], 'height': item['info']['height'], 'duration_ms': item['info']['duration_ms'],
            'conversion': item['conversion'], 'frames': len(track['frames']), 'visible_frames': track['visible_frames'],
            'rule_version': track['rule_version'], **paths}
        atomic_json(MANIFEST, doc)
    doc['home_demo_id'] = 'user-1575'
    doc['active_demo_ids'] = ['user-1575'] + ['user-' + number for number in numbers]
    doc['home_demo_ids'] = list(doc['active_demo_ids'])
    atomic_json(MANIFEST, doc)
    report = project_path('data/verification/round3/user-demo-analysis.json')
    previous = json.loads(report.read_text(encoding='utf-8'))
    previous['playlist_review'] = {**data, 'active_demo_ids': doc['active_demo_ids'], 'paid_ai_calls': 0, 'classification_training_runs': 0}
    previous['playlist_review']['limitation'] = '新6段没有检出姿态issue；1692/1693/1694推胸识别基本一致，1688/1689/1690存在动作混淆。身体倾斜未被当前规则完整覆盖，不能将未检出当作整体动作正确。'
    atomic_json(report, previous)
    print(json.dumps({'published': doc['active_demo_ids']}), flush=True)


def add_landscape_demos(numbers):
    """追加用户确认的高位下拉；保留完整主体，裁去空天花板并横屏补景。"""
    import subprocess
    from PIL import Image
    from scripts.prepare_user_demos import FFMPEG,track
    requested=list(dict.fromkeys(numbers))
    if not requested or set(requested)-{'1688','1689'}:raise ValueError('仅处理本次确认的两段素材')
    document=json.loads(MANIFEST.read_text(encoding='utf-8'))
    records=[]
    for number in requested:
        source=SOURCE/('IMG_'+number+'.MOV');demo_id='user-'+number
        if not source.is_file():raise FileNotFoundError(source)
        if demo_id in document.get('demos',{}):raise ValueError('该演示已登记，请先核对，避免重复导入')
        before=(source.stat().st_size,source.stat().st_mtime_ns);source_sha=digest(source)
        video=project_path('data/datasets/user-demos/'+demo_id+'.mp4')
        image=project_path('fitness_app/static/demo/'+demo_id+'-poster.jpg')
        timeline=project_path('fitness_app/static/demo/'+demo_id+'-track.json')
        for target in (video,image,timeline):
            if target.exists():raise ValueError('目标已存在，拒绝覆盖：'+str(target))
        # 转正后的1080×1920竖画面只去掉480px空天花板，手、躯干、髋与脚均保留。
        tone='crop=1080:1440:0:480,fps=30,zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p'
        graph='[0:v]'+tone+',split=2[main][surround];[surround]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,gblur=sigma=60,eq=contrast=0.75:brightness=-0.06:saturation=0.6[bg];[main]scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[out]'
        print(json.dumps({'stage':'landscape_convert','source':source.name}),flush=True)
        subprocess.run([str(FFMPEG),'-hide_banner','-loglevel','error','-n','-threads','2','-i',str(source),'-filter_complex_threads','2','-filter_complex',graph,'-map','[out]','-an','-sn','-map_metadata','-1','-c:v','libx264','-threads','2','-preset','fast','-crf','22','-pix_fmt','yuv420p','-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart',str(video)],check=True,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
        info=probe_video(video)
        if (info['width'],info['height'])!=(1280,720):raise ValueError('横屏输出尺寸不符')
        width,height=poster(video,image)
        print(json.dumps({'stage':'demo_pose_track','source':source.name,'duration_ms':info['duration_ms']}),flush=True)
        video_asset=asset(video);data=track(video,timeline,'lat_pulldown',info,video_asset['sha256'])
        issues=['身体侧倾'] if number=='1688' else []
        data.update(classification_source='user_confirmed_action',user_reported_issues=issues,
                    user_reported_quality='needs_adjustment' if issues else 'correct_reference',
                    new_classification_inferences=0)
        for row in data['frames']:
            if row['feedback']['state']=='clear':row['feedback']['clear_label']='你标记的问题尚未检出' if issues else '已检查项目未见偏差'
        atomic_json(timeline,data)
        item={'id':demo_id,'action_id':'lat_pulldown','title':'高位下拉',
            'display_title':'高位下拉（错误演示）' if issues else '高位下拉（正确演示）','source_name':source.name,'source_sha256':source_sha,'source_bytes':before[0],
            'source_type':'user_supplied','source_url':None,'source_label':'用户提供的本机训练视频','credit':'用户提供',
            'license':'用户授权用于本项目演示','use_scope':'本项目本机演示与纠错验证；不加入识别训练集、不上传第三方AI',
            'non_endorsement':'用户确认的素材描述与模型实际判项分开，不代表独立准确率验证。',
            'demonstration_intent':'user_confirmed_adjustment' if issues else 'user_confirmed_reference',
            'user_reported_issues':issues,'user_reported_quality':data['user_reported_quality'],
            'action_source':'user_confirmed','independent_error_truth':False,
            'width':1280,'height':720,'duration_ms':info['duration_ms'],'video':video_asset,
            'poster':{**asset(image),'width':width,'height':height},'track':asset(timeline),
            'frames':len(data['frames']),'visible_frames':data['visible_frames'],'rule_version':data['rule_version'],
            'conversion':{'codec':'h264','pixel_format':'yuv420p','fps':30,'audio_removed':True,
                'hdr_to_sdr':True,'rotation_applied':True,'source_display_size':[1080,1920],
                'foreground_crop_pixels':[0,480,1080,1440],'foreground_size':[540,720],
                'foreground_offset':[370,0],'layout':'16:9 full-body foreground with blurred scene sides',
                'filter_complex':graph,'duration_trimmed':False}}
        if before!=(source.stat().st_size,source.stat().st_mtime_ns) or source_sha!=digest(source):raise ValueError('处理期间原片变化，未登记')
        records.append(item)
        print(json.dumps({'stage':'prepared','demo':demo_id,'video_bytes':video_asset['bytes'],'visible_frames':data['visible_frames'],'frames':len(data['frames'])}),flush=True)
    for item in records:document.setdefault('demos',{})[item['id']]=item
    document['active_demo_ids']=list(dict.fromkeys(document.get('active_demo_ids',[])+[item['id'] for item in records]))
    if all(key in document['demos'] for key in ('user-1694','user-1689')):
        document['home_demo_id']='user-1694'
        document['home_demo_ids']=['user-1694','user-1689']
    atomic_json(MANIFEST,document)
    print(json.dumps({'published':[item['id'] for item in records],'active':document['active_demo_ids'],'home':document['home_demo_ids'],'paid_ai_calls':0,'originals_unchanged':True}),flush=True)


def reliable_demo_legs(points,body,width,height,threshold=.5):
    """演示与实时界面使用同一套腿点可靠性规则。"""
    from fitness_app.skeleton import reliable_lower_limb_points
    return reliable_lower_limb_points(points,body,width,height,threshold)


def shift_feedback_times(value,offset):
    """只平移绝对证据时间；校准年龄、窗口长度等持续量保持不变。"""
    if isinstance(value,list):return [shift_feedback_times(v,offset) for v in value]
    if not isinstance(value,dict):return value
    return {k:v-offset if k in ('start_ms','end_ms','evidence_start_ms','evidence_end_ms') and type(v) in (int,float)
            else shift_feedback_times(v,offset) for k,v in value.items()}


def refine_landscape_skeletons(numbers):
    """复用原模型判项，用现有RTMPose复核展示腿点；不改动作识别和计数。"""
    from fitness_app.media import video_frames
    from fitness_app.rtmpose_input import RTMPose
    document=json.loads(MANIFEST.read_text(encoding='utf-8'));model=RTMPose('body')
    for number in dict.fromkeys(numbers):
        demo_id='user-'+number;item=document['demos'][demo_id]
        video=project_path(item['video']['path']);timeline=project_path(item['track']['path'])
        if asset(video)!=item['video'] or asset(timeline)!=item['track']:raise ValueError('资源身份改变，停止复核')
        data=json.loads(timeline.read_text(encoding='utf-8'))
        if data.get('skeleton_refinement'):raise ValueError('轨迹已复核，拒绝重复覆盖')
        width,height=data['width'],data['height'];offset=item['conversion']['foreground_offset'];size=item['conversion']['foreground_size']
        region=[offset[0],offset[1],offset[0]+size[0],offset[1]+size[1]]
        pending=iter(data['frames']);row=next(pending,None);calls=0;shown=Counter()
        for stamp,rgb in video_frames(video,probe_video(video)):
            if row is None:break
            if stamp<row['t']:continue
            if stamp!=row['t']:raise ValueError('复核帧与原轨迹时间不匹配')
            original=row['p'];row['model_p']=original
            candidates=[p for p in original if p is not None]
            body={'keypoints':[{'coordinate_valid':False}]*17}
            if row['people']==1 and len(candidates)>=8:
                x0=min(p[0] for p in candidates)*width;x1=max(p[0] for p in candidates)*width
                y0=min(p[1] for p in candidates)*height;y1=max(p[1] for p in candidates)*height
                bounds=[max(region[0],x0-(x1-x0)*.12),max(region[1],y0-(y1-y0)*.08),
                        min(region[2],x1+(x1-x0)*.12),min(region[3],y1+(y1-y0)*.08)]
                if bounds[2]-bounds[0]>=.1*width and bounds[3]-bounds[1]>=.2*height:
                    body=model.detect(rgb,bounds);calls+=1
            row['p']=reliable_demo_legs(original,body,width,height)
            # 保留真实模型输出用于追溯；显示坐标只能来自本帧实际推理。
            row['leg_observation']=[{k:p[k] for k in ('x','y','score','coordinate_valid') if k in p} for p in body['keypoints'][11:]]
            for knee in (25,26):
                if row['p'][knee] is not None:shown[str(knee)]+=1
            row=next(pending,None)
        if row is not None:raise ValueError('视频提前结束，复核未完成')
        data['skeleton_refinement']={'version':1,'scope':'display_lower_limbs_only','model_sha256':model.resource['sha256'],
            'model':'rtmpose-body','confidence_threshold':.5,'whole_leg_requires_hip_knee_ankle':True,
            'unsupported_heel_and_toe_points_omitted':True,'original_coordinates':'frames.model_p',
            'model_feedback_unchanged':True,'new_pose_inferences':calls,'visible_leg_frames':dict(shown)}
        atomic_json(timeline,data);item['track']=asset(timeline)
        atomic_json(MANIFEST,document)
        print(json.dumps({'demo':demo_id,'refined_frames':calls,'visible_legs':dict(shown)},ensure_ascii=False),flush=True)


def trim_chest_press_intro(start_ms):
    """从原MOV重新编码去掉准备段，展示轨迹按同一起点平移，保留原模型判项。"""
    import subprocess
    from collections import Counter
    from scripts.prepare_user_demos import FFMPEG
    document=json.loads(MANIFEST.read_text(encoding='utf-8'));item=document['demos']['user-1694']
    if item['conversion'].get('duration_trimmed'):raise ValueError('已裁过准备段，请先复核，拒绝重复裁切')
    if type(start_ms) is not int or start_ms!=5500:raise ValueError('本次已核对准备段起点为5500ms')
    source=SOURCE/item['source_name'];before=(source.stat().st_size,source.stat().st_mtime_ns)
    if digest(source)!=item['source_sha256']:raise ValueError('原片身份改变，停止裁切')
    stage=project_path('.tmp/user-demo-1688-1689')
    if not stage.is_dir():raise ValueError('本次临时目录不可用')
    video=project_path(item['video']['path']);timeline=project_path(item['track']['path']);image=project_path(item['poster']['path'])
    if asset(video)!=item['video'] or asset(timeline)!=item['track']:raise ValueError('资源改变，停止裁切')
    target=stage/'user-1694.mp4'
    subprocess.run([str(FFMPEG),'-hide_banner','-loglevel','error','-n','-threads','2','-ss',str(start_ms/1000),
        '-i',str(source),'-map','0:v:0','-an','-sn','-map_metadata','-1','-vf',','.join(item['conversion']['filters']),
        '-filter_threads','2','-c:v','libx264','-threads','2','-preset','fast','-crf','23','-pix_fmt','yuv420p',
        '-color_primaries','bt709','-color_trc','bt709','-colorspace','bt709','-movflags','+faststart',str(target)],
        check=True,cwd=ROOT,creationflags=subprocess.CREATE_NO_WINDOW)
    info=probe_video(target);data=json.loads(timeline.read_text(encoding='utf-8'));old_sha=item['video']['sha256']
    data['frames']=[row for row in data['frames'] if start_ms<=row['t']<start_ms+info['duration_ms']]
    for index,row in enumerate(data['frames']):
        row['t']-=start_ms
        row['feedback']=shift_feedback_times(row['feedback'],start_ms)
        if 'frame_seq' in row:row['frame_seq']=index
    data['events']=[{**e,'start_ms':e['start_ms']-start_ms,'end_ms':e['end_ms']-start_ms}
        for e in data['events'] if start_ms<=e['start_ms']<e['end_ms']<start_ms+info['duration_ms']]
    data['timeline_edit']={'source_video_sha256':old_sha,'trim_start_ms':start_ms,
        'original_timestamps_offset_ms':start_ms,'model_feedback_reused_from_original_timeline':True,
        'feedback_time_base':'trimmed_video_ms_negative_before_start'}
    data['source_sha256']=digest(target);data['frames_count']=len(data['frames'])
    data['visible_frames']=sum(any(p is not None for p in row['p']) for row in data['frames'])
    data['feedback_state_counts']=dict(Counter(row['feedback']['state'] for row in data['frames']))
    data['posture_status_counts']=dict(Counter(check['code']+':'+check['status']
        for row in data['frames'] for check in row['feedback']['checks']))
    staged_image=stage/'user-1694-poster.jpg';width,height=poster(target,staged_image)
    if before!=(source.stat().st_size,source.stat().st_mtime_ns) or digest(source)!=item['source_sha256']:raise ValueError('原片处理期间变化')
    if not data['frames'] or not 0<=data['frames'][0]['t']<100 or data['frames'][-1]['t']>=info['duration_ms']:raise ValueError('裁切时间轴无效')
    target.replace(video);staged_image.replace(image);atomic_json(timeline,data)
    item.update(video=asset(video),track=asset(timeline),poster={**asset(image),'width':width,'height':height},
        duration_ms=info['duration_ms'],frames=len(data['frames']),visible_frames=data['visible_frames'])
    item['conversion'].update(duration_trimmed=True,trim_start_ms=start_ms,trim_end_ms=None)
    item['display_annotation'].update(scope='time_range',start_ms=2000,end_ms=info['duration_ms'])
    atomic_json(MANIFEST,document)
    print(json.dumps({'demo':'user-1694','trim_start_ms':start_ms,'duration_ms':info['duration_ms'],'frames':len(data['frames']),'original_unchanged':True}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--publish', nargs=3, choices=NUMBERS)
    parser.add_argument('--add-landscape',nargs='+',choices=('1688','1689'))
    parser.add_argument('--refine-skeleton',nargs='+',choices=('1688','1689'))
    parser.add_argument('--trim-chest-intro',action='store_true')
    args = parser.parse_args()
    if args.trim_chest_intro:trim_chest_press_intro(5500)
    elif args.refine_skeleton:refine_landscape_skeletons(args.refine_skeleton)
    elif args.add_landscape:add_landscape_demos(args.add_landscape)
    elif args.publish:publish(args.publish)
    else:review()
