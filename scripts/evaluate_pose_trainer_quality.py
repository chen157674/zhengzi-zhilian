"""用固定第三方粗标签轨迹检查现有器械动作规则，不训练、不改生产阈值。"""
from collections import Counter
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment

configure_environment()
import numpy as np
from fitness_app.gym_quality import evaluate
from fitness_app.posture_rules import RULES, policy_version
from fitness_app.posture_engine import create_analysis_engine
from fitness_app.coaching import coaching_observation, committed_issues
from fitness_app.research_extract import atomic_json


SOURCE_ROOT = ROOT / 'third_party/pose-trainer'
SOURCE_COMMIT = '7d54c527722c89feeb71d98ebd1ba585b0886338'
OUTPUT = ROOT / 'data/verification/round3/pose-trainer-quality-evaluation.json'
EXPECTED_SOURCE = {
    'license_sha256': '3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986',
    'readme_sha256': '0c361a1152f7a85786180980a6aa6f212ae8654ed4778ccedfbe0a51ce7d39fe',
    'pose_sha256': '451d11425568182909fbdd5a86a9925223cefb7438f27a6f684f3b5120c02e1a',
    'utils_sha256': '917b6b294280edef942bb4be85a8d7b7c559fdbd23be285b19bb46a1d22f0079',
    'dataset_files': 112,
    'dataset_bytes': 3727520,
    'dataset_manifest_sha256': 'b2878fdd0b3392559d8d22e3856b72a11f51973fb9456d214edc268144af74b2',
}
OPENPOSE_TO_MEDIAPIPE = {
    2: 12, 3: 14, 4: 16, 5: 11, 6: 13, 7: 15,
    8: 24, 9: 26, 10: 28, 11: 23, 12: 25, 13: 27,
}
CASES = {
    'front_raise': {
        'folder': 'frontraise',
        'product_action_id': 'dumbbell_front_raise',
        'rules': ('dumbbell_front_raise.trunk_sway',),
    },
    'shoulder_press': {
        'folder': 'shoulderpress',
        'product_action_id': 'dumbbell_shoulder_press',
        'rules': ('dumbbell_shoulder_press.trunk_sway',),
    },
    'bicep_curl': {
        'folder': 'bicep',
        'product_action_id': 'hammer_curl',
        'rules': ('hammer_curl.trunk_sway', 'hammer_curl.upper_arm_swing'),
    },
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def natural_key(path):
    return [int(part) if part.isdigit() else part for part in re.split(r'(\d+)', path.name)]


def source_bundle():
    files = sorted((SOURCE_ROOT / 'poses_compressed').rglob('*'))
    files = [path for path in files if path.is_file()]
    lines = []
    for path in files:
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        lines.append(f'{relative}\t{path.stat().st_size}\t{sha(path)}')
    digest = hashlib.sha256(('\n'.join(lines) + '\n').encode()).hexdigest()
    return {'dataset_files': len(files), 'dataset_bytes': sum(path.stat().st_size for path in files),
            'dataset_manifest_sha256': digest}


def verify_source():
    actual = {
        'license_sha256': sha(SOURCE_ROOT / 'LICENSE'),
        'readme_sha256': sha(SOURCE_ROOT / 'README.md'),
        'pose_sha256': sha(SOURCE_ROOT / 'pose.py'),
        'utils_sha256': sha(SOURCE_ROOT / 'utils.py'),
        **source_bundle(),
    }
    if actual != EXPECTED_SOURCE:
        raise ValueError('pose-trainer固定来源已变化，不能复用原标签结论')
    sources = json.loads((ROOT / 'third_party/sources.json').read_text(encoding='utf-8'))
    entry = next((item for item in sources if item.get('repo') == 'stevenzchen/pose-trainer'), None)
    if not entry or entry.get('commit') != SOURCE_COMMIT or entry.get('license') != 'GPL-3.0':
        raise ValueError('pose-trainer来源清单缺失或版本不匹配')
    return actual


def _point(x, y, confidence, width, height):
    present = confidence != 0.0 and all(math.isfinite(value) for value in (x, y, confidence)) and x >= 0 and y >= 0
    if not present:
        return {'valid': False}
    # 上游的存在契约是confidence非零；不将其数值冒充MediaPipe visibility。
    return {'x': x / width, 'y': y / height, 'z': 0.0, 'visibility': 1.0,
            'presence': 1.0, 'valid': True, 'source_confidence': confidence}


def frames_from_sequence(path):
    sequence = np.load(path, allow_pickle=False)
    if sequence.ndim != 3 or sequence.shape[1:] != (18, 3) or not len(sequence):
        raise ValueError('pose-trainer轨迹形状无效: ' + path.name)
    if not np.isfinite(sequence).all():
        raise ValueError('pose-trainer轨迹含非有限值: ' + path.name)
    width = max(1.0, float(sequence[:, :, 0].max()) + 1.0)
    height = max(1.0, float(sequence[:, :, 1].max()) + 1.0)
    frames = []
    for index, source in enumerate(sequence):
        points = [{'valid': False} for _ in range(33)]
        for source_index, target_index in OPENPOSE_TO_MEDIAPIPE.items():
            points[target_index] = _point(*map(float, source[source_index]), width, height)
        frames.append({'captured_ms': index * 33, 'frame_seq': index, 'width': width, 'height': height,
                       'identity_accepted': True, 'posture_view': 'side',
                       'framing': {'person_count': 1, 'code': 'VISIBLE', 'landmarks': points}})
    return frames


def _metrics(rows):
    counts = Counter()
    for row in rows:
        actual_bad = row['coarse_label'] == 'bad'
        if row['prediction'] == 'unassessable':
            counts['unassessable'] += 1
        elif actual_bad and row['prediction'] == 'issue':
            counts['tp'] += 1
        elif not actual_bad and row['prediction'] == 'issue':
            counts['fp'] += 1
        elif not actual_bad:
            counts['tn'] += 1
        else:
            counts['fn'] += 1
    tp, fp, tn, fn = (counts[key] for key in ('tp', 'fp', 'tn', 'fn'))
    assessed = tp + fp + tn + fn
    return {'sequences': len(rows), 'tp': tp, 'fp': fp, 'tn': tn, 'fn': fn,
            'unassessable': counts['unassessable'],
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'false_positive_rate': fp / (fp + tn) if fp + tn else None,
            'coverage': assessed / len(rows) if rows else 0.0}


def evaluate_case(name, case):
    rows = []
    folder = SOURCE_ROOT / 'poses_compressed' / case['folder']
    for path in sorted(folder.glob('*.npy'), key=natural_key):
        frames = frames_from_sequence(path)
        results = {}
        for code in case['rules']:
            result = evaluate(code, frames, 'side', RULES[code][4])
            results[code] = result
        statuses = {result['status'] for result in results.values()}
        prediction = 'issue' if 'issue' in statuses else 'not_observed' if 'not_observed' in statuses else 'unassessable'
        rows.append({'file': path.name, 'sha256': sha(path), 'frames': len(frames),
                     'coarse_label': 'good' if 'good' in path.name else 'bad',
                     'prediction': prediction, 'rule_results': results})
    return {'name': name, 'source_action': case['folder'], 'product_action_id': case['product_action_id'],
            'evaluated_rules': list(case['rules']), 'metrics': _metrics(rows), 'rows': rows,
            'label_scope': 'source_filename_good_bad_only',
            'specific_error_accuracy_eligible': False}


def evaluate_all():
    source = verify_source()
    implementation = {name: sha(ROOT / name) for name in (
        'fitness_app/gym_quality.py', 'fitness_app/gym_motion.py',
        'fitness_app/posture_rules.py', 'fitness_app/posture_engine.py',
        'fitness_app/coaching.py', 'scripts/evaluate_pose_trainer_quality.py')}
    cases = {name: evaluate_case(name, case) for name, case in CASES.items()}
    return {'schema_version': 1, 'source': {'repo': 'stevenzchen/pose-trainer',
                'commit': SOURCE_COMMIT, 'license': 'GPL-3.0', **source,
                'presence_contract': 'OpenPose confidence != 0 means present; numeric confidence is not MediaPipe visibility'},
            'rule_version': policy_version(), 'implementation_sha256': implementation, 'cases': cases,
            'curl_production_stream': evaluate_curl_stream(),
            'new_pose_inferences': 0, 'new_visual_inferences': 0, 'new_training_runs': 0,
            'independent_coarse_label_evaluation': True, 'independent_specific_error_accuracy_passed': False,
            'release_candidate': False,
            'scope': '独立good/bad轨迹的粗粒度规则检查；不含原视频、具体错误部位/侧别/方向真值，不能替代R3具体纠错验收'}


def evaluate_curl_stream():
    rows=[]
    for path in sorted((SOURCE_ROOT/'poses_compressed/bicep').glob('*.npy'),key=natural_key):
        engine=create_analysis_engine('standing_dumbbell_curl')
        counts=Counter();codes=set();first=None;frames=frames_from_sequence(path)
        for frame in frames:
            output=engine.process(frame,{'stream_epoch':'source-curl','coaching_view':'side'})
            for item in coaching_observation(output).get('posture',{}).get('items',[]):
                counts[item['code']+':'+item['status']]+=1
            issues=committed_issues(output)
            if issues and first is None:first=frame['captured_ms']
            codes.update(item['code'] for item in issues)
        rows.append({'file':path.name,'sha256':sha(path),'frames':len(frames),
                     'coarse_label':'good' if 'good' in path.name else 'bad',
                     'prediction':'issue' if codes else 'not_observed' if any(k.endswith(':not_observed') for k in counts) else 'unassessable',
                     'first_issue_ms':first,'issue_codes':sorted(codes),'status_counts':dict(counts)})
    return {'rows':rows,'metrics':_metrics(rows),'frame_interval_ms':33,
            'time_basis':'工程回放时钟；原始fps未提供，不能当作实测提示延迟',
            'view_basis':'工程侧面请求；无独立机位标签',
            'specific_error_accuracy_eligible':False}


def run(output=OUTPUT):
    report = evaluate_all()
    atomic_json(output, report)
    print(json.dumps({name: value['metrics'] for name, value in report['cases'].items()},
                     ensure_ascii=False, indent=2), flush=True)
    print(json.dumps({'curl_production_stream':report['curl_production_stream']['metrics']}),flush=True)
    print('Saved coarse-label evaluation; production thresholds and specific-error acceptance remain unchanged.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    run(parser.parse_args().output)
