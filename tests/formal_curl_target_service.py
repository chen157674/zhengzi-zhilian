"""真实Full双侧目标浏览器夹具；原片仅读，短H.264仅在本次隔离目录。"""
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path

import av
from flask import jsonify, send_file
from fitness_app import app as app_module
from fitness_app.auth import initialize_admin
from fitness_app.paths import ROOT, project_path
from fitness_app.research_data import load_dataset
from fitness_app.runtime import serve

folder = project_path(Path(os.environ['FITNESS_DATABASE_PATH']).parent)
assert folder == ROOT/'.tmp/tests/ui-step19-curl-target'
source = next(s for s in load_dataset() if s.path ==
              'seven-exercises/bicep_curl/good/subject_001_bicep_curl_good_front.mp4')
assert source.partition in {'train', 'development'}
assert hashlib.sha256(source.absolute_path.read_bytes()).hexdigest() == source.sha256
original_stat = source.absolute_path.stat()
target = project_path(folder/'curl-target.mp4')
assert not target.exists()
seconds = int(os.environ.get("FITNESS_CURL_FIXTURE_SECONDS", "12"))
assert seconds in {12, 16}
frame_limit = seconds * 15
frames = 0
with av.open(str(source.absolute_path)) as incoming, av.open(str(target), 'w') as outgoing:
    stream = outgoing.add_stream('libx264', rate=15)
    stream.width, stream.height, stream.pix_fmt = 810, 1440, 'yuv420p'
    stream.codec_context.thread_count = 2
    stream.options = {'crf': '18', 'preset': 'veryfast'}
    for source_frame in incoming.decode(video=0):
        if frames == frame_limit:
            break
        frame = source_frame.reformat(width=810, height=1440, format='yuv420p')
        frame.pts, frame.time_base = frames, Fraction(1, 15)
        for packet in stream.encode(frame):
            outgoing.mux(packet)
        frames += 1
    for packet in stream.encode():
        outgoing.mux(packet)
assert frames == frame_limit
assert source.absolute_path.stat().st_mtime_ns == original_stat.st_mtime_ns
provenance = {'source_path': source.path, 'source_sha256': source.sha256,
              'partition': source.partition, 'subject_group': source.subject_group,
              'source_unchanged': True, 'fixture_frames': frames, 'fixture_fps': 15,
              'fixture_size': [810, 1440], 'fixture_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
              'transform': f'first {frame_limit} frames; HEVC to H.264 libx264 crf18 veryfast, original dimensions, yuv420p, no audio',
              'scope': 'temporary browser transport fixture only; source good label is not repetition ground truth'}

metadata = next(s for s in json.loads((ROOT/'data/datasets/manifest.json').read_text(encoding='utf-8'))['sources'] if s['id'] == source.dataset_id)
provenance.update(source_url=metadata['url'], attribution=metadata['attribution'], licence=metadata['licence']['short_name'], licence_url=metadata['licence']['url'])

original = app_module.create_app
bootstrap = original({'TESTING': True, 'DATABASE_PATH': os.environ['FITNESS_DATABASE_PATH']})
db = bootstrap.extensions['database']
initialize_admin(db, 'Curl_Admin', 'curl-target-fixture-password-123')
db.dispose()


def testing_app(config):
    app = original({**config, 'TESTING': True, 'DATABASE_PATH': os.environ['FITNESS_DATABASE_PATH']})

    @app.get('/__fixture/curl-source')
    def video():
        return send_file(target, conditional=True)

    @app.get('/__fixture/curl-source-info')
    def info():
        return jsonify(provenance)

    return app


app_module.create_app = testing_app
serve(0)
