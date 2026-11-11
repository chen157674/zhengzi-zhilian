"""按需获取官方纠错输入候选；不替换生产模型，不把下载成功计作纠错通过。"""
import argparse
import hashlib
import json
import sys
import time
import subprocess
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment, project_path
from fitness_app.research_extract import atomic_json
from fitness_app.database import maintenance_lock

DIRECTORY = ROOT/'data/models'
MANIFEST = DIRECTORY/'correction-resources.json'
RESOURCES = {
    'hands': {'file': 'hand_landmarker.task', 'model': 'mediapipe-hand-float16-1',
        'url': 'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task',
        'mirror': 'https://huggingface.co/camenduru/HandRefiner/resolve/9567791b60899721946f8f4713fc45766cc3531c/hand_landmarker.task',
        'expected_sha256': 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1',
        'hash_source': 'https://huggingface.co/camenduru/HandRefiner/blob/9567791b60899721946f8f4713fc45766cc3531c/hand_landmarker.task',
        'documentation': 'https://ai.google.dev/edge/mediapipe/solutions/vision/hand_landmarker',
        'purpose': 'hand_landmarks_not_grip_error_labels', 'minimum_bytes': 1000000, 'maximum_bytes': 15000000},
    'yolo': {'file': 'yolo11n-pose.pt', 'model': 'ultralytics-yolo11n-pose-v8.3.0',
        'url': 'https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n-pose.pt',
        'mirror': 'https://huggingface.co/Ultralytics/YOLO11/resolve/ef787449d1fe015a77a27ca4d375ed0fde69495c/yolo11n-pose.pt',
        'expected_sha256': '869e83fcdffdc7371fa4e34cd8e51c838cc729571d1635e5141e3075e9319dc0',
        'hash_source': 'https://huggingface.co/Ultralytics/YOLO11/blob/ef787449d1fe015a77a27ca4d375ed0fde69495c/yolo11n-pose.pt',
        'documentation': 'https://docs.ultralytics.com/models/yolo11/',
        'purpose': 'body_pose_comparison_not_pretrained_grip_correction', 'minimum_bytes': 6000000, 'maximum_bytes': 10000000},
}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(name, transport='urllib', mirror=False, direct=False):
    spec = RESOURCES[name]
    target = project_path(DIRECTORY/spec['file'])
    registry = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'schema_version': 1, 'resources': {}}
    previous = registry['resources'].get(name)
    if target.exists():
        if not previous or sha(target) != previous['sha256'] or previous['url'] != spec['url']:
            raise ValueError('已有文件与登记不一致，不覆盖')
        return {'resource': name, 'reused': True, 'bytes': target.stat().st_size}
    partial = target.with_suffix(target.suffix+'.part')
    if partial.exists():
        raise ValueError('已有下载中间文件，需先核实归属，不覆盖')
    download_url = spec.get('mirror', spec['url']) if mirror else spec['url']
    try:
        if transport == 'curl':
            command = ['curl.exe', '--fail', '--location', '--silent', '--show-error', '--retry', '1',
                       '--connect-timeout', '15', '--max-time', '90', '--max-filesize', str(spec['maximum_bytes']),
                       '--output', str(partial), download_url]
            proxy = urllib.request.getproxies().get('https')
            if direct:
                command[1:1] = ['--noproxy', '*']
            elif proxy:
                command[1:1] = ['--proxy', proxy]
            subprocess.run(command, check=True, capture_output=True)
            size = partial.stat().st_size
        else:
            request = urllib.request.Request(download_url, headers={'User-Agent': 'fitness-assistant-correction-models/1'})
            with urllib.request.urlopen(request, timeout=40) as response, partial.open('xb') as stream:
                size = 0
                while chunk := response.read(1024*1024):
                    size += len(chunk)
                    if size > spec['maximum_bytes']:
                        raise ValueError('模型超出预期大小')
                    stream.write(chunk)
        if not spec['minimum_bytes'] <= size <= spec['maximum_bytes']:
            raise ValueError('下载内容过小，不登记为模型')
        with zipfile.ZipFile(partial) as bundle:
            if bundle.testzip() is not None:
                raise ValueError('模型包损坏')
        if spec.get('expected_sha256') and sha(partial) != spec['expected_sha256']:
            raise ValueError('模型与已登记公开版本SHA不一致')
        record = {**spec, 'path': target.relative_to(ROOT).as_posix(), 'bytes': size, 'sha256': sha(partial),
                  'download_url': download_url, 'downloaded_at_ms': int(time.time()*1000), 'runtime_validated': False,
                  'correction_accuracy_validated': False, 'production_enabled': False}
        partial.replace(target)
        with maintenance_lock(MANIFEST):
            registry = json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'schema_version': 1, 'resources': {}}
            registry['resources'][name] = record
            atomic_json(MANIFEST, registry)
        return {'resource': name, 'reused': False, 'bytes': size, 'sha256': record['sha256']}
    finally:
        if partial.exists():
            partial.unlink()


def main():
    configure_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resource', choices=(*RESOURCES, 'all'))
    parser.add_argument('--transport', choices=('urllib','curl'), default='urllib')
    parser.add_argument('--mirror', action='store_true', help='主下载源不可达时使用已固定版本与SHA的公开副本')
    parser.add_argument('--direct', action='store_true', help='仅本次下载直连，不修改系统代理')
    args = parser.parse_args()
    for name in RESOURCES if args.resource == 'all' else (args.resource,):
        print(json.dumps(prepare(name, args.transport, args.mirror, args.direct), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
