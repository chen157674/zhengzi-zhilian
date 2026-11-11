"""新运行素材的短段资格检查；原参数不变，原始姿态只提取一次。"""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, project_path, configure_environment
configure_environment()
from fitness_app.pose_input import ContentPoseService, input_policy_snapshot
from fitness_app.media import video_frames
from fitness_app.frame_pipeline import framing
from fitness_app.sessions import IdentityContinuity
from fitness_app.counting_resources import create_counter

CASES = [('dvids-548443', 19000, 31700), ('dvids-590723', 27100, 31600)]
FOLDER = project_path('data/verification/step-19')


def main():
    sources = json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']
    path = FOLDER/'runtime-candidate-poses.json.gz'
    cache = json.loads(gzip.decompress(path.read_bytes())) if path.exists() else {}
    results = []
    for source_id, start, end in CASES:
        source = next(s for s in sources if s['id'] == source_id)
        video = project_path(source['path'])
        assert hashlib.sha256(video.read_bytes()).hexdigest() == source['sha256']
        identity = {'source_sha256': source['sha256'], 'source_path': source['path'],
                    'interval_ms': [start, end], 'input_policy': input_policy_snapshot(ContentPoseService),
                    'decoder_sha256': hashlib.sha256((ROOT/'fitness_app/media.py').read_bytes()).hexdigest()}
        fresh = source_id not in cache
        if not fresh and cache[source_id]['identity'] != identity:
            raise ValueError('已有姿态缓存身份不符，先审核差异，不自动重算或覆盖')
        if fresh:
            rows = []
            with ContentPoseService(video=True) as pose:
                for stamp, rgb in video_frames(video, {'origin_ms': 0, 'rotation': 0}):
                    if stamp < start:
                        continue
                    if stamp > end:
                        break
                    rows.append({'stamp': stamp, 'width': int(rgb.shape[1]), 'height': int(rgb.shape[0]),
                                 'result': pose.detect(rgb, stamp)})
            cache[source_id] = {'identity': identity, 'rows': rows}
            path.write_bytes(gzip.compress(json.dumps(cache, separators=(',', ':')).encode('utf-8'), mtime=0))
        counter = create_counter(source['action_id'])
        continuity = IdentityContinuity()
        events, transitions = [], []
        codes, reasons, phases = Counter(), Counter(), Counter()
        previous = None
        for seq, row in enumerate(cache[source_id]['rows']):
            quality = framing(source['action_id'], row['result'])
            frame = {'frame_seq': seq, 'captured_ms': row['stamp'], 'width': row['width'],
                     'height': row['height'], 'framing': quality}
            codes[quality['code']] += 1
            if not quality['usable'] or not continuity.accept(frame):
                counter.reset('tracking_lost')
                state = ('unobservable', quality['code'] if not quality['usable'] else 'identity_rejected')
            else:
                result = counter.process(frame, {'stream_epoch': 'new-runtime-input', 'identity_accepted': True})
                events.extend(result['events'])
                observation = result['observation']['sides']['none']
                state = (observation['phase'], observation['reason'])
            phases[state[0]] += 1
            reasons[state[1] or 'observable'] += 1
            if state != previous:
                transitions.append({'source_ms': row['stamp'], 'phase': state[0], 'reason': state[1]})
                previous = state
        result = {'source_id': source_id, 'action_id': source['action_id'], 'identity': identity,
                  'fresh_pose_inference': fresh, 'frames': len(cache[source_id]['rows']),
                  'framing_codes': dict(codes), 'phases': dict(phases), 'reasons': dict(reasons),
                  'reference_count': len(events), 'events': events, 'transitions': transitions,
                  'final_state': previous, 'independent_ground_truth': False,
                  'scope': 'Short native decoded input through current Full/counter; not browser JPEG, sustained performance, sagittal quality or independent accuracy acceptance'}
        results.append(result)
        print(json.dumps({k: result[k] for k in ('source_id', 'fresh_pose_inference', 'frames', 'framing_codes', 'phases', 'reference_count', 'final_state')}, ensure_ascii=False), flush=True)
    report = {'cases': results, 'counter_sha256': hashlib.sha256((ROOT/'fitness_app/action_counting.py').read_bytes()).hexdigest(),
              'parameters_unchanged': True, 'frozen_research_corpus_unchanged': True}
    (FOLDER/'runtime-candidate-inputs.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


if __name__ == '__main__':
    main()
