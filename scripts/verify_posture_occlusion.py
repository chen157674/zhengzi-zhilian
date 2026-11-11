"""用固定真实训练片的既有姿态缓存验证遮挡撤回，不训练、不构造错误真值。"""
from collections import Counter
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment, project_path

configure_environment()
import numpy as np
from fitness_app.automatic_recognition import body_frame
from fitness_app.frame_pipeline import analysis_framing
from fitness_app.gym_quality import evaluate
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.posture_rules import policy_version
from fitness_app.research_extract import atomic_json
from fitness_app.source_partitions import enforce_source_policy


CASES = (
    'youtube-IXeRFh9nUwk-142000-157000',
    'youtube-i14hrj7l7CI-61000-91000',
    'youtube-7BkgqzC6WsM-119000-140000',
    'dvids-636933-2000-15000',
)
OUTPUT = ROOT / 'data/verification/round3/posture-occlusion-replay.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_case(segment, source):
    cache = project_path(ROOT / 'data/verification/step-22' / (segment['id'] + '-sequence.npz'))
    path = project_path(source['path'])
    if sha(path) != segment['source_sha256'] or source['sha256'] != segment['source_sha256']:
        raise ValueError('真实源身份改变')
    cache_sha = sha(cache)
    with np.load(cache, allow_pickle=False) as data:
        metadata = json.loads(str(data['metadata']))
        if any(metadata[k] != segment[k] for k in ('source_sha256', 'start_ms', 'end_ms', 'crop')):
            raise ValueError('原姿态窗口不匹配')
        for name, digest in metadata['pose_code'].items():
            if sha(project_path(ROOT / 'fitness_app' / name)) != digest:
                raise ValueError('姿态实现已变化，不能假定原缓存代表当前提取')
        model = json.loads((ROOT / 'data/models/manifest.json').read_text(encoding='utf-8'))
        if model['sha256'] != metadata['pose_model_sha256']:
            raise ValueError('原姿态模型身份改变')
        frames = []
        for i, stamp in enumerate(data['timestamps']):
            people = int(data['people'][i])
            poses = [[dict(zip(('x', 'y', 'z', 'visibility', 'presence'), map(float, p)))
                      for p in data['points'][i]]] if people == 1 else [[]] * people
            detected = {'poses': poses, 'content_bounds': data['bounds'][i].tolist()}
            w, h = map(int, data['sizes'][i])
            frame = body_frame(detected, int(stamp), w, h, i)
            frame['framing'] = analysis_framing(segment['action_id'], detected)
            frames.append(frame)
    return frames, {'path': cache.relative_to(ROOT).as_posix(), 'sha256': cache_sha,
                    'metadata': metadata}


def run(only=None):
    manifest_path = ROOT / 'data/datasets/development-movements/gym-segments.json'
    segments = json.loads(manifest_path.read_text(encoding='utf-8'))['segments']
    selected = [next(s for s in segments if s['id'] == key) for key in (only or CASES)]
    original = json.loads((ROOT / 'data/verification/step-22/semantic-video-features.json').read_text(encoding='utf-8'))
    original = {s['id']: s for s in original['entries']}
    for segment in selected:
        if segment['partition'] != 'train' or any(segment[k] != original[segment['id']][k]
                for k in ('partition', 'source_sha256', 'group', 'action_id', 'start_ms', 'end_ms')):
            raise ValueError('不读取新验证或预留素材，也不更改原分区')
    enforce_source_policy(selected, operation='inference')
    sources = json.loads((ROOT / 'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))
    sources = {s['id']: s for s in sources['sources']}
    implementation = {n: sha(ROOT / n) for n in (
        'fitness_app/gym_quality.py', 'fitness_app/posture_engine.py',
        'fitness_app/coaching.py', 'fitness_app/skeleton.py', 'scripts/verify_posture_occlusion.py')}
    rows = []
    if only and OUTPUT.exists():
        previous = json.loads(OUTPUT.read_text(encoding='utf-8'))
        if previous['rule_version'] != policy_version() or any(
                previous['implementation_sha256'][n] != digest for n, digest in implementation.items()
                if n != 'scripts/verify_posture_occlusion.py'):
            raise ValueError('受影响实现已变，不能复用之前片段的检查')
        for row in previous['cases']:
            if row['id'] in only:
                continue
            if sha(project_path(ROOT / row['pose_cache']['path'])) != row['pose_cache']['sha256']:
                raise ValueError('之前已检查的姿态缓存改变')
            row.setdefault('verification_script_sha256', previous['implementation_sha256']['scripts/verify_posture_occlusion.py'])
            rows.append(row)
    for segment in selected:
        frames, cache = load_case(segment, sources[segment['source_id']])
        engine = create_analysis_engine(segment['action_id'])
        statuses, faults, windows = Counter(), Counter(), []
        for frame in frames:
            output = engine.process(frame, {'stream_epoch': 'cached-real', 'coaching_view': 'auto'})
            posture = output['observation']['posture']
            for item in posture['items']:
                statuses[item['code'] + ':' + item['status']] += 1
                if item['status'] == 'unassessable':
                    continue
                # 只扰动内存中的当前关节有效位，原片、原缓存和其余坐标均不变。
                window = [f for f in engine.frames if frame['captured_ms'] - f['captured_ms'] <= 2400]
                window = window[:-1] + [copy.deepcopy(window[-1])]
                indices = (15,) if item['code'].endswith('arm_asymmetry') else (23, 24)
                for index in indices:
                    window[-1]['framing']['landmarks'][index]['valid'] = False
                result = evaluate(item['code'], window, posture['view'], item['evidence']['threshold'])
                if result['status'] != 'unassessable':
                    raise AssertionError('遮挡后仍返回姿态结论: ' + item['code'])
                faults[item['code']] += 1
                windows.append(frame['captured_ms'])
        if not faults:
            raise ValueError('此固定片段没有可评估判项，不能用空检查宣布通过')
        if sha(project_path(ROOT / cache['path'])) != cache['sha256']:
            raise ValueError('检查过程中原姿态缓存改变')
        row = {k: segment[k] for k in ('id', 'action_id', 'source_id', 'source_sha256', 'group', 'partition', 'start_ms', 'end_ms')}
        row.update(pose_cache=cache, frames=len(frames), reference_status_counts=dict(statuses),
                   current_joint_mask_checks=dict(faults), first_checked_source_ms=min(windows),
                   last_checked_source_ms=max(windows), mask_is_synthetic=True,
                   independent_specific_error_truth=False, correction_precision=None,
                   verification_script_sha256=implementation['scripts/verify_posture_occlusion.py'])
        rows.append(row)
        print(json.dumps({'id': segment['id'], 'frames': len(frames),
                          'checked_reference_windows': sum(faults.values())}), flush=True)
    report = {'schema_version': 1, 'rule_version': policy_version(), 'cases': rows,
              'implementation_sha256': implementation,
              'new_pose_inferences': 0, 'new_visual_inferences': 0, 'new_training_runs': 0,
              'correction_precision': None, 'independent_accuracy_passed': False,
              'scope': '真实训练片的缓存接线与内存关节遮挡检查；不是独立错误正确率、原始遮挡真值或新增独立视频'}
    atomic_json(OUTPUT, report)
    print('Saved reference-only replay; independent correction accuracy remains unmeasured.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only', action='append', choices=CASES, help='只检查指定片段，保留未受影响的旧结果')
    run(parser.parse_args().only)
