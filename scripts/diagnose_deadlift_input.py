"""硬拉开发素材输入对照；复用原尺寸缓存，四种新输入各提取一次。"""
from collections import Counter
import gzip
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment, project_path
configure_environment()
import numpy as np
from PIL import Image
from fitness_app.counting_resources import create_counter
from fitness_app.frame_pipeline import decode_jpeg, framing
from fitness_app.media import video_frames
from fitness_app.pose_input import ContentPoseService, input_policy_snapshot
from fitness_app.research_data import load_dataset
from fitness_app.sessions import IdentityContinuity
from diagnose_cycles import cached_frames

ACTION = 'conventional_deadlift'
MODES = ('portrait_rgb', 'padded_rgb', 'portrait_jpeg', 'padded_jpeg')
FOLDER = project_path('data/verification/step-19')


def transform(rgb, mode):
    # 明确是Pillow对照，不冒充浏览器Canvas的重采样或JPEG字节。
    resized = np.asarray(Image.fromarray(rgb).resize((402, 720), Image.Resampling.BILINEAR))
    if mode.startswith('padded'):
        output = np.zeros((720, 1280, 3), dtype=np.uint8)
        output[:, 439:841] = resized
    else:
        output = np.ascontiguousarray(resized)
    if mode.endswith('jpeg'):
        with BytesIO() as buffer:
            Image.fromarray(output).save(buffer, format='JPEG', quality=80, subsampling=2)
            output = decode_jpeg(buffer.getvalue())
    return output


def diagnose(frames):
    counter = create_counter(ACTION)
    continuity = IdentityContinuity()
    events, losses, transitions = [], [], []
    reasons, codes = Counter(), Counter()
    previous = None
    for frame in frames:
        quality = frame['framing']
        accepted = quality['usable'] and continuity.accept(frame)
        codes[quality['code']] += 1
        if not accepted:
            prior = counter.trackers['none'].phase
            counter.reset('tracking_lost')
            state = ('seeking_start', quality['code'] if not quality['usable'] else 'identity_rejected')
            losses.append({'stamp': frame['captured_ms'], 'prior_phase': prior, 'reason': state[1],
                          'invalid_right': [i for i in (12, 14, 16, 24, 26, 28)
                                            if not quality['landmarks'] or not quality['landmarks'][i]['valid']]})
        else:
            result = counter.process(frame, {'stream_epoch': 'input-factor', 'identity_accepted': True})
            events.extend(result['events'])
            obs = result['observation']['sides']['none']
            state = (obs['phase'], obs['reason'])
        reasons[state[1] or 'observable'] += 1
        if state != previous:
            transitions.append({'stamp': frame['captured_ms'], 'phase': state[0], 'reason': state[1]})
            previous = state
    return {'frames': len(frames), 'reference_count': len(events), 'events': events,
            'framing_codes': dict(codes), 'reason_counts': dict(reasons), 'losses': losses,
            'transitions': transitions}


def main():
    sample = next(s for s in load_dataset() if s.path == 'mydeadlift/Gerakan Benar/GB_S02_R29.mp4')
    assert sample.partition in {'train', 'development'}
    assert hashlib.sha256(sample.absolute_path.read_bytes()).hexdigest() == sample.sha256
    identity = {'source_sha256': sample.sha256, 'partition': sample.partition,
                'input_policy': input_policy_snapshot(ContentPoseService),
                'transform_protocol': 'pillow-bilinear-402x720-center-439-jpeg80-subsampling2-1',
                'packages': {name: version(name) for name in ('Pillow', 'av', 'numpy')},
                'decoder_sha256': hashlib.sha256((ROOT/'fitness_app/media.py').read_bytes()).hexdigest()}
    cache = FOLDER/'deadlift-input-factors.json.gz'
    archive = json.loads(gzip.decompress(cache.read_bytes())) if cache.exists() else {'identity': identity, 'modes': {}}
    if archive['identity'] != identity:
        raise ValueError('现有对照缓存身份不同，先核差异，不能自动重新推理或覆盖')
    decoded = None
    fresh_modes = []
    for mode in MODES:
        if mode in archive['modes']:
            continue
        if decoded is None:
            decoded = list(video_frames(sample.absolute_path, {'origin_ms': 0, 'rotation': 0}))
        rows = []
        with ContentPoseService(video=True) as pose:
            for stamp, rgb in decoded:
                transformed = transform(rgb, mode)
                result = pose.detect(transformed, stamp)
                rows.append({'captured_ms': stamp, 'width': int(transformed.shape[1]),
                             'height': int(transformed.shape[0]), 'result': result})
        archive['modes'][mode] = rows
        cache.write_bytes(gzip.compress(json.dumps(archive, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), mtime=0))
        fresh_modes.append(mode)
        print(json.dumps({'extracted': mode, 'frames': len(rows)}), flush=True)
    baseline = json.loads((ROOT/'data/experiments/baseline.json').read_text(encoding='utf-8'))
    native = cached_frames(sample, baseline)
    comparisons = {'native_cached': diagnose(native)}
    for mode, rows in archive['modes'].items():
        frames = [{**{k: row[k] for k in ('captured_ms', 'width', 'height')}, 'frame_seq': seq,
                   'framing': framing(ACTION, row['result'])} for seq, row in enumerate(rows)]
        comparisons[mode] = {**diagnose(frames), 'crop_geometries': [list(g) for g in sorted({tuple(row['result']['input_transform']['crop_pixels']) for row in rows})],
                             'tracker_resets': sum(row['result']['input_transform']['tracker_reset'] for row in rows)}
    report = {'identity': identity, 'scope': 'One development clip, matched native source timestamps, full 2x2 padding/JPEG input comparison; not independent accuracy or a browser-byte-exact reproduction',
              'fresh_modes': fresh_modes, 'native_pose_reused': True,
              'counter_sha256': hashlib.sha256((ROOT/'fitness_app/action_counting.py').read_bytes()).hexdigest(),
              'cases': comparisons}
    (FOLDER/'deadlift-input-factors.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({mode: {k: result[k] for k in ('frames', 'reference_count', 'framing_codes')}
                      for mode, result in comparisons.items()}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
