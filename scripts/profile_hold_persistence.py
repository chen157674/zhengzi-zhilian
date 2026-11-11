"""平板支撑吞吐未达标后的隔离保存耗时诊断；不读取相机、不推理、不作为准确率。"""
import cProfile
import hashlib
import json
import pstats
import statistics
import sys
from unittest.mock import patch
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT, configure_environment
configure_environment()
sys.path.insert(0, str(ROOT/'tests'))
from test_formal_records import RecordTests
from sqlalchemy.pool import QueuePool
from fitness_app import database as database_module


def main():
    output = ROOT/'data/verification/step-19/hold-persistence-profile.json'
    pooled = '--pooled' in sys.argv
    sources = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in (
        'fitness_app/persistence.py', 'fitness_app/records.py', 'fitness_app/database.py',
        'fitness_app/db_schema.py', 'scripts/profile_hold_persistence.py')}
    previous = json.loads(output.read_text(encoding='utf-8')) if output.exists() else {}
    variant = 'test-only QueuePool size 5 overflow 0' if pooled else 'production configuration'
    cached = next((r for r in previous.get('runs', []) if r.get('sources') == sources and r.get('variant') == variant), {})
    if cached:
        print('Same diagnostic already recorded; no repeated writes.')
        return
    fixture = RecordTests()
    factory = database_module.create_engine
    def pooled_engine(*args, **kwargs):
        kwargs.update(poolclass=QueuePool, pool_size=5, max_overflow=0)
        return factory(*args, **kwargs)
    if pooled:
        with patch.object(database_module, 'create_engine', pooled_engine):
            fixture.setUp()
    else:
        fixture.setUp()
    try:
        sid = fixture.running('forearm_plank')
        timings = []
        profile = cProfile.Profile()
        profile.enable()
        for i in range(500):
            start = perf_counter()
            result = fixture.writer.submit(fixture.user, sid, fixture.auth, 'intervals', {
                'set_index': 1, 'active_ms': (i+1)*67,
                'intervals': [{'kind': 'hold', 'start_ms': i*67, 'end_ms': (i+1)*67,
                               'algorithm_version': 'isolated-persistence-diagnostic'}]})
            timings.append((perf_counter()-start)*1000)
            assert result['hold_ms'] == (i+1)*67 and result['save_state'] == 'clean'
        profile.disable()
        stats = pstats.Stats(profile)
        rows = []
        for (file, line, name), (primitive, calls, own, cumulative, callers) in stats.stats.items():
            if file.startswith(str(ROOT)) or file == '~':
                rows.append({'function': name, 'file': file.removeprefix(str(ROOT)+'\\'),
                             'line': line, 'calls': calls, 'own_ms': own*1000,
                             'cumulative_ms': cumulative*1000})
        rows.sort(key=lambda r: r['cumulative_ms'], reverse=True)
        result = {'sources': sources, 'scope': '500 sequential actual interval transactions in an isolated test database, synthetic contiguous intervals; no model/browser; cProfile overhead included; not end-to-end bottleneck attribution',
                  'transactions': 500, 'mean_ms': statistics.mean(timings),
                  'p95_ms': sorted(timings)[474], 'first_100_mean_ms': statistics.mean(timings[:100]),
                  'last_100_mean_ms': statistics.mean(timings[-100:]),
                  'functions': rows[:30]}
        result['variant'] = variant
        result['pool_variant'] = type(fixture.db.engine.pool).__name__
        previous.setdefault('schema_version', 1)
        previous.setdefault('runs', []).append(result)
        output.write_text(json.dumps(previous, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({k: v for k, v in result.items() if k not in ('sources', 'functions')}, ensure_ascii=False))
        print(json.dumps(rows[:12], ensure_ascii=False))
    finally:
        for name in ('video_jobs', 'session_manager', 'preview_manager', 'media_inputs'):
            fixture.app.extensions[name].stop()
        fixture.tearDown()


if __name__ == '__main__':
    main()
