"""从本次录屏剪辑 OPC 预览；原始素材只读，配音另行使用已核实的免费额度。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'OPC专项赛/健身项目视频素材/剪辑工程.json'
BUILD = ROOT / '.tmp/opc-completion'
FFMPEG = Path(r'E:\07_Projects\07_MonetizationTests\02_KnowledgeVideos\open-source\huashu-art-motion\.venv\Scripts\ffmpeg.exe')
FPS = 30


def inside(value):
    path = Path(value).resolve()
    path.relative_to(ROOT.resolve())
    return path


def render(item):
    index, section, local_start, clip, total_clips = item
    source = inside(ROOT / 'OPC专项赛/健身项目视频素材' / clip['source'])
    if not source.is_file() or clip['source'].startswith('杂项（'):
        raise ValueError('素材不存在或被明确排除')
    start, end, speed = clip['in'], clip['out'], clip['speed']
    if not 0 <= start < end or speed <= 0:
        raise ValueError('剪辑区间无效')
    duration = (end - start) / speed
    # 单画面自然录屏：只裁掉浏览器与桌面边缘，按原比例铺满16:9，不添加背景或分栏。
    vf = f'setpts=(PTS-STARTPTS)/{speed},fps={FPS},crop=1792:1008:64:132,scale=1920:1080,setsar=1,format=yuv420p'
    output = inside(BUILD / f'footage-cut-{index:02d}.mp4')
    command = [str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-y', '-threads', '2',
               '-ss', str(start), '-t', str(end-start), '-i', str(source),
               '-filter_threads', '1', '-vf', vf, '-map', '0:v:0', '-an',
               '-t', f'{duration:.6f}', '-r', str(FPS), '-c:v', 'h264_nvenc', '-preset', 'p4',
               '-rc', 'vbr', '-cq', '20', '-b:v', '0', '-pix_fmt', 'yuv420p',
               '-video_track_timescale', '30000', '-movflags', '+faststart', str(output)]
    result = subprocess.run(command, cwd=BUILD, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f'片段{index}编码失败：' + result.stderr.decode('utf-8', errors='replace')[-2000:])
    print(f'片段 {index+1}/{total_clips} 完成：{duration:.2f}秒，{clip["mode"]}', flush=True)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', type=int)
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    inside(BUILD)
    if not BUILD.is_dir():
        raise ValueError('已确认的制作目录不存在')
    for variable in ('TEMP', 'TMP'):
        os.environ[variable] = str(BUILD)
    os.environ['XDG_CACHE_HOME'] = str(inside(ROOT/'.cache/xdg'))
    data = json.loads(inside(MANIFEST).read_text(encoding='utf-8'))
    timeline = []
    for number, section in enumerate(data['sections'], 1):
        section = {**section, 'number': number}
        local_start = 0.0
        for clip in section['clips']:
            timeline.append((len(timeline), section, local_start, clip, 0))
            local_start += (clip['out'] - clip['in']) / clip['speed']
    timeline = [(*item[:-1], len(timeline)) for item in timeline]
    if args.only is not None:
        render(timeline[args.only])
        return
    if not 0 < data['duration'] < 300:
        raise ValueError('时长必须小于5分钟')
    with ThreadPoolExecutor(max_workers=max(1,min(args.workers,3))) as pool:
        for task in as_completed([pool.submit(render,item) for item in timeline]):
            task.result()
    listing = inside(BUILD/'footage-concat.txt')
    listing.write_text('\n'.join(f"file 'footage-cut-{i:02d}.mp4'" for i in range(len(timeline)))+'\n',encoding='utf-8')
    output = inside(ROOT/data['output'])
    subprocess.run([str(FFMPEG), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0',
                    '-i', str(listing), '-c', 'copy', '-movflags', '+faststart', str(output)], cwd=BUILD, check=True)
    print(json.dumps({'output':str(output),'planned_seconds':data['duration'],'voiceover':False},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
