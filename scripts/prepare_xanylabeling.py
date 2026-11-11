"""准备独立桌面试标：同源视频链接、原生sidecar和待复核时间窗，不自动确认错误。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
from fitness_app.media import probe_video,video_frames
from fitness_app.research_extract import atomic_json

WORKSPACE=ROOT/'data/annotation/x-anylabeling'
CLASSES=['待复核','身体侧倾','握持问题_待细分','身体侧倾且握持问题','明显折腕','左右握持不一致','前后借力摆动','未见身体侧倾','准备或调整','看不清','躯干前倾','头颈后仰']
COLORS=['#89939C','#E69F00','#D55E00','#CC5C72','#E07A5F','#9C6644','#8E6CBB','#009E73','#56B4E9','#66717E','#7FFFD4','#FFD700']


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def segment_signature(segment):
    return hashlib.sha256(json.dumps(segment,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def prepare():
    import av
    workspace=project_path(WORKSPACE)
    manifest_path=workspace/'manifest.json'
    if manifest_path.exists():
        previous=json.loads(manifest_path.read_text(encoding='utf-8'))
        if previous.get('owner')!='fitness-xanylabeling':raise ValueError('已有工作区归属不符')
        if {v['number'] for v in previous.get('videos',[])}!={'1693','1694'}:raise ValueError('已有准备未完成，请先核查工作区，不覆盖标注')
        for video in previous['videos']:
            if digest(project_path(video['path']))!=video['source_sha256']:raise ValueError('已有视频摘要不匹配')
            sidecar=json.loads(project_path(video['sidecar']).read_text(encoding='utf-8'))
            if sidecar.get('type')!='video_classification' or not sidecar.get('segments'):raise ValueError('已有sidecar不完整')
        for name in ('classes.txt','.xanylabelingrc','first-review.gif'):
            if not (workspace/name).is_file():raise ValueError(f'已有工作区缺少{name}，请先核查，不重建标注')
        print(json.dumps({'workspace':str(workspace),'reused':True,'message':'保留已有sidecar与用户修改，不重建'}),flush=True)
        return previous
    if workspace.exists() and any(workspace.iterdir()):raise ValueError('已有非空工作区，不能覆盖')
    workspace.mkdir(parents=True,exist_ok=True)
    video_dir=workspace/'videos';video_dir.mkdir()
    assets=json.loads((ROOT/'data/presentation-assets.json').read_text(encoding='utf-8'))
    review=json.loads((ROOT/'data/verification/round3/correction-input-review.json').read_text(encoding='utf-8'))
    prior={item['number']:item for item in review['videos']}
    manifest={'schema_version':1,'owner':'fitness-xanylabeling','tool_version':'4.2.2',
              'classes':CLASSES,'private_media_upload_allowed':False,'drafts_are_ground_truth':False,'videos':[]}
    atomic_json(manifest_path,manifest)
    for number in ('1693','1694'):
        record=assets['demos']['user-'+number];source=project_path(record['video']['path'])
        if digest(source)!=record['video']['sha256']:raise ValueError('视频源摘要不匹配')
        target=video_dir/f'{number}.mp4'
        try:os.link(source,target);storage='ntfs_hardlink_same_project'
        except OSError:shutil.copy2(source,target);storage='copy'
        info=probe_video(target)
        with av.open(str(target)) as container:fps=float(container.streams.video[0].average_rate)
        segments=[]
        for index,sample in enumerate(prior[number]['samples'],1):
            center=sample['t'];start=max(0,center-1200);end=min(info['duration_ms'],center+1200)
            segments.append({'id':f'candidate_{number}_{index}','label':'待复核','start_ms':start,'end_ms':end,
                'start_frame':round(start*fps/1000),'end_frame':round(end*fps/1000),
                'description':'Codex预选时间窗，尚未确认错误。先看上身和握持；拿不准就选“看不清”。'})
        sidecar={'version':'1.0.0','type':'video_classification','video':target.name,'fps':fps,
            'duration_ms':info['duration_ms'],'width':info['width'],'height':info['height'],
            'labels':CLASSES,'label_colors':dict(zip(CLASSES,COLORS)),'segments':segments}
        atomic_json(target.with_suffix('.json'),sidecar)
        manifest['videos'].append({'number':number,'source_path':source.relative_to(ROOT).as_posix(),
            'source_sha256':record['video']['sha256'],'path':target.relative_to(ROOT).as_posix(),
            'sidecar':target.with_suffix('.json').relative_to(ROOT).as_posix(),'storage':storage,
            'duration_ms':info['duration_ms'],'fps':fps,'initial_segments':segments})
        atomic_json(manifest_path,manifest)
    (workspace/'classes.txt').write_text('\n'.join(CLASSES)+'\n',encoding='utf-8')
    # 官方--work-dir读取此配置；视频标签由原生sidecar提供，不设置任何云端Key。
    (workspace/'.xanylabelingrc').write_text('language: zh_CN\ntheme: light\nauto_save: true\nstore_data: false\n',encoding='utf-8')
    first=manifest['videos'][1];segment=first['initial_segments'][0]
    make_review_gif(project_path(first['path']),segment['start_ms'],segment['end_ms'],workspace/'first-review.gif')
    print(json.dumps({'workspace':str(workspace),'videos':2,'candidate_segments':6,'first_video':'1694',
                      'first_start_ms':segment['start_ms'],'first_end_ms':segment['end_ms']},ensure_ascii=False),flush=True)
    return manifest


def make_review_gif(path,start,end,destination):
    from PIL import Image
    info=probe_video(path);frames=[];last=-1000
    for stamp,rgb in video_frames(path,info,start_ms=start,end_ms=end):
        if stamp-last<120:continue
        last=stamp;image=Image.fromarray(rgb);image.thumbnail((640,360));frames.append(image.convert('P',palette=Image.Palette.ADAPTIVE,colors=128))
    if not frames:raise ValueError('首段复核区间未解码到画面')
    frames[0].save(destination,save_all=True,append_images=frames[1:],duration=133,loop=0,optimize=False)


def inspect():
    manifest=json.loads((WORKSPACE/'manifest.json').read_text(encoding='utf-8'))
    rows=[]
    for video in manifest['videos']:
        if digest(project_path(video['path']))!=video['source_sha256']:raise ValueError('标注视频已改变')
        sidecar=json.loads(project_path(video['sidecar']).read_text(encoding='utf-8'))
        if sidecar.get('type')!='video_classification' or sidecar.get('version')!='1.0.0':raise ValueError('sidecar格式不符')
        seen=set()
        for segment in sidecar['segments']:
            if segment['id'] in seen:raise ValueError('片段ID重复')
            seen.add(segment['id'])
            if not 0<=segment['start_ms']<segment['end_ms']<=video['duration_ms']:raise ValueError('标注时间越界')
            label=segment['label']
            if label not in sidecar['labels']:raise ValueError('片段标签未在本视频类别表登记')
            status='pending' if label=='待复核' else 'unknown' if label=='看不清' else 'needs_subtype' if label in ('握持问题_待细分','身体侧倾且握持问题') else 'annotation_pending_review'
            review=manifest.get('segment_reviews',{}).get(segment['id'],{})
            source=review.get('source','unverified') if review.get('segment_sha256')==segment_signature(segment) else 'unverified'
            rows.append({'video':video['number'],'id':segment['id'],'label':label,'status':status,
                         'annotation_source':source,'training_eligible':False,
                         'start_ms':segment['start_ms'],'end_ms':segment['end_ms']})
    print(json.dumps({'segments':rows,'automatic_training':False},ensure_ascii=False),flush=True)


if __name__=='__main__':
    configure_environment()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect',action='store_true');args=parser.parse_args()
    inspect() if args.inspect else prepare()
