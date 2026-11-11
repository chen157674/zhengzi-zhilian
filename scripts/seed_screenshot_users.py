"""创建截图用的独立场景账号；合成来源只存内部字段，不改既有用户。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from types import SimpleNamespace
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fitness_app.paths import configure_environment, project_path
configure_environment()
from sqlalchemy import select, update, func
from fitness_app import db_schema as s
from fitness_app.auth import password_hash, validate_email, validate_password
from fitness_app.coach_retests import CoachRetests
from fitness_app.database import Database, json_text
from fitness_app.nutrition_profiles import NutritionProfiles
from fitness_app.posture_rules import describe
from fitness_app.records import SessionStore, TERMINAL
from fitness_app.retests import Retests
from fitness_app.training_info import TrainingInfoStore

DATASET = 'screenshot-personas-1'
PROVENANCE = {'seed_dataset': DATASET, 'synthetic_data': True, 'model_detected': False}
DAYS = [13, 11, 9, 7, 6, 5, 4, 3, 2, 1, 0, 0, 6, 4, 0]
PERSONAS = [
    {
        'seed_key': 'zlxt.demo01@outlook.com',
        'email': 'linyu.fit@outlook.com', 'nickname': '林屿', 'avatar': 'avatar-05',
        'profile': dict(height_cm=172, weight_kg=64, age_years=21, experience='beginner',
                        goals=['movement_quality', 'training_habit'], available_minutes=30,
                        equipment=['bodyweight', 'dumbbell']),
        'sex': 'male',
        'actions': ['bodyweight_squat', 'forearm_plank', 'bodyweight_squat', 'forearm_plank',
                    'standing_dumbbell_curl', 'bodyweight_squat', 'bodyweight_squat',
                    'standing_dumbbell_curl', 'bodyweight_squat', 'bodyweight_squat',
                    'forearm_plank', 'bodyweight_squat'],
    },
    {
        'seed_key': 'zlxt.demo02@outlook.com',
        'email': 'xuning.fit@outlook.com', 'nickname': '许宁', 'avatar': 'avatar-12',
        'profile': dict(height_cm=180, weight_kg=76, age_years=24, experience='intermediate',
                        goals=['muscle_gain', 'strength'], available_minutes=50,
                        equipment=['machine', 'cable', 'dumbbell', 'bodyweight']),
        'sex': 'male',
        'actions': ['lat_pulldown', 'machine_chest_press', 'bodyweight_squat', 'seated_cable_row',
                    'lat_pulldown', 'bodyweight_squat', 'machine_chest_press', 'bodyweight_squat',
                    'lat_pulldown', 'bodyweight_squat', 'machine_chest_press', 'machine_chest_press',
                    'bodyweight_squat', 'bodyweight_squat', 'bodyweight_squat'],
    },
    {
        'seed_key': 'zlxt.demo03@outlook.com',
        'email': 'zhouhe.fit@outlook.com', 'nickname': '周禾', 'avatar': 'avatar-19',
        'profile': dict(height_cm=168, weight_kg=58, age_years=22, experience='intermediate',
                        goals=['movement_quality', 'strength'], available_minutes=40,
                        equipment=['bodyweight', 'machine', 'dumbbell']),
        'sex': 'female',
        'actions': ['bodyweight_squat', 'machine_chest_press', 'standing_dumbbell_curl',
                    'bodyweight_squat', 'machine_chest_press', 'bodyweight_squat',
                    'standing_dumbbell_curl', 'bodyweight_squat', 'machine_chest_press',
                    'bodyweight_squat', 'machine_chest_press', 'bodyweight_squat'],
    },
]


def stable_id(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, DATASET + '/' + value))


class SeedTransaction:
    """复用一次外层事务，使账号、记录与档案全部成功后才提交。"""
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self, write=True):
        yield self.connection


def finding(rule, state, start, end, side='none'):
    reason = 'landmarks_not_visible' if state == 'unassessable' else ''
    message, suggestion = describe(rule['code'], state, reason)
    return dict(code=rule['code'], status=state, reason=reason, start_ms=start, end_ms=end,
                rule_version=rule['rule_version'], evidence_level='reference',
                body_parts=rule['body_parts'], message=message, suggestion=suggestion,
                evidence={'view': 'side', **PROVENANCE}, side=side)


def apply_screenshot_distribution(connection, user_ids):
    """调整场景记录的显示分布，百分比仍由实际保存的次数计算。"""
    changed = 0
    for uid in user_ids:
        rows = connection.execute(select(s.analysis_sessions).where(
            s.analysis_sessions.c.user_id == uid,
            s.analysis_sessions.c.action_id == 'bodyweight_squat',
            func.json_extract(s.analysis_sessions.c.config_json, '$.seed_dataset') == DATASET,
        ).order_by(s.analysis_sessions.c.started_at_ms)).mappings().all()
        if len({row['capability_version'] for row in rows}) > 1:
            raise ValueError('截图场景的同一动作需要使用同一能力快照，不能混合检测版本')
        curve = ([.52] * max(0, len(rows) - 6) + [.48, .36, .40, .27, .17, .11])[-len(rows):]
        for row, ratio in zip(rows, curve):
            events = connection.execute(select(s.action_events).where(
                s.action_events.c.session_id == row['id']).order_by(s.action_events.c.event_seq)).mappings().all()
            available = []
            values = {}
            for event in events:
                items = json.loads(event['assessment_json'])
                values[event['id']] = items
                item = next((v for v in items if v['code'] == 'squat.range'), None)
                if item and item['status'] != 'unassessable':
                    available.append((event['id'], item))
            count = round(len(available) * ratio)
            positions = {i * len(available) // count for i in range(count)}
            dirty = set()
            for index, (eid, item) in enumerate(available):
                status = 'issue' if index in positions else 'not_observed'
                if item['status'] != status:
                    message, suggestion = describe('squat.range', status)
                    item.update(status=status, reason='', message=message, suggestion=suggestion)
                    dirty.add(eid)
            for eid in dirty:
                connection.execute(update(s.action_events).where(s.action_events.c.id == eid).values(
                    assessment_json=json_text(values[eid], list)))
            if dirty:
                connection.execute(update(s.analysis_sessions).where(s.analysis_sessions.c.id == row['id']).values(
                    state_version=s.analysis_sessions.c.state_version + 1))
                changed += 1
    return changed


def create_record(store, info, clock, uid, auth, persona, index, person_index, now, catalogue):
    action_id = persona['actions'][index]
    action = catalogue[action_id]
    start = now - DAYS[index] * 86400000 - (150 if index == 10 else 30) * 60000
    clock[0] = start
    reps = 8 + (index % 3) * 2
    sets = 3 if person_index == 1 else 2
    seconds = 30 + (index % 3) * 15
    target = dict(sets=sets, value=seconds if action['unit'] == 'seconds' else reps,
                  unit=action['unit'], rest_seconds=45)
    counting = bool(action.get('counting_available'))
    config = dict(action_id=action_id, capability_version=action['capability_version'],
                  source='camera', mode='target' if counting else 'free',
                  target=target if counting else None, coaching_view='side',
                  exercise_basis='user_selected', **PROVENANCE)
    sid = store.create(uid, auth, config, stable_id(f'{uid}/record/{index}'))['session_id']
    store.set_state(uid, sid, 'running', s.new_id(), auth_id=auth)
    rules = [rule for rule in action['issues'] if rule.get('status') == 'reference']
    sequence = 0
    cursor = 0
    active = 0
    cut_short = index == 4
    for group in range(1, sets + 1):
        if group > 1 and counting:
            store.set_state(uid, sid, 'running', s.new_id(), auth_id=auth)
        if action['unit'] == 'seconds':
            duration = seconds * 1000
            observations = [
                finding(r, 'issue' if ri == 0 and index == 1 and group == 2 else 'not_observed',
                        cursor, cursor + duration)
                for ri, r in enumerate(rules)
            ]
            active += duration
            store.append_posture(uid, sid, group, observations, auth, active)
            clock[0] = start + cursor + duration
            store.append_intervals(uid, sid, group, [
                dict(kind='hold', start_ms=cursor, end_ms=cursor + duration,
                     algorithm_version=DATASET)
            ], auth, active)
            cursor += duration
        else:
            sides = ['left', 'right'] if action['unit'] == 'per_side_reps' else ['none']
            rounds = reps // 2 if cut_short and group == sets else reps
            for rep in range(rounds):
                for side in sides:
                    sequence += 1
                    left, right = cursor, cursor + 4000
                    active += right - left
                    clock[0] = start + right
                    items = []
                    for ri, rule in enumerate(rules):
                        issue_count = [3, 2, 4, 1, 3, 2, 1, 2, 1, 1, 2, 1][index % 12]
                        state = 'issue' if ri == 0 and group == 1 and rep < issue_count else 'not_observed'
                        if ri == 0 and rep == rounds - 1 and index % 4 == 0:
                            state = 'unassessable'
                        item = finding(rule, state, left, right, side)
                        items.append(item)
                    # 已开放计数的动作保留周期，其他动作只保留姿态时间窗。
                    # 同一判项不重复写两种统计单位，复测才能比较相同条件。
                    if counting:
                        store.append_event(uid, sid, dict(
                            event_seq=sequence, set_index=group, side=side,
                            start_ms=left, end_ms=right, active_ms=active,
                            algorithm_version=DATASET, feature_schema_version=DATASET,
                            assessments=[{k: v for k, v in item.items() if k != 'side'} for item in items],
                        ), auth)
                    else:
                        store.append_posture(uid, sid, 1, items, auth, active)
                    cursor = right
        current = store.snapshot(uid, sid)
        if current['status'] in TERMINAL:
            break
        if not counting and group == sets:
            store.set_state(uid, sid, 'ended_early' if cut_short else 'completed', s.new_id(),
                            auth_id=auth, reason='user_ended' if cut_short else 'analysis_completed')
            break
        if cut_short and group == sets:
            clock[0] = start + cursor
            store.set_state(uid, sid, 'ended_early', s.new_id(), auth_id=auth, reason='user_ended')
            break
        cursor += 45000
    clock[0] = start + cursor
    note = {
        'expected_version': 0, 'difficulty': ['moderate', 'easy', 'moderate', 'hard'][index % 4],
        'note': [
            '先稳定站姿，再逐渐增加训练量。',
            '保持自然呼吸，回程适当放慢。',
            '这一组的节奏比较稳定，下次保持相同幅度。',
            '最后一组略吃力，下次先保持当前负重。',
        ][index % 4],
    }
    load = {'standing_dumbbell_curl': [5, 8, 6][person_index],
            'machine_chest_press': [15, 30, 20][person_index],
            'lat_pulldown': [20, 35, 25][person_index],
            'seated_cable_row': [20, 30, 20][person_index]}.get(action_id)
    if load is not None:
        note.update(load_value=load, load_unit='kg',
                    load_basis='per_hand' if action_id == 'standing_dumbbell_curl' else 'total')
    info.write(uid, auth, note, s.new_id(), sid=sid)
    assert store.snapshot(uid, sid)['status'] in TERMINAL
    return sid


def seed(path, apply=False):
    path = project_path(path)
    if not path.is_file():
        raise ValueError('数据库不存在，不能创建空库代替当前预览')
    db = Database(path)
    try:
        with db.transaction(False) as c:
            if c.scalar(select(s.app_meta.c.schema_version)) != s.SCHEMA_VERSION:
                raise ValueError('数据库版本不符，未执行迁移')
            # 使用目标库已经登记的最新能力快照，避免独立进程的默认开关生成另一套版本。
            catalogue = {}
            for row in c.execute(select(s.action_capabilities).order_by(
                    s.action_capabilities.c.created_at_ms, s.action_capabilities.c.capability_version)).mappings():
                catalogue[row['action_id']] = json.loads(row['definition_json'])
            required = {action for persona in PERSONAS for action in persona['actions']}
            if not required.issubset(catalogue):
                raise ValueError('目标数据库缺少所需动作能力，未修改')
            existing = {r['username_key']: dict(r) for r in c.execute(
                select(s.users.c.id, s.users.c.username_key).where(
                    s.users.c.username_key.in_([p['email'] for p in PERSONAS]))).mappings()}
            if existing:
                if len(existing) != len(PERSONAS):
                    raise ValueError('部分邮箱已存在，不自动覆盖或补写')
                for p in PERSONAS:
                    if existing[p['email']]['id'] != stable_id(p['seed_key']):
                        raise ValueError('邮箱属于已有用户，未修改')
                return {'created': False, 'accounts': [
                    {'email': p['email'], 'nickname': p['nickname'],
                     'records': c.scalar(select(func.count()).select_from(s.analysis_sessions).where(
                         s.analysis_sessions.c.user_id == stable_id(p['seed_key'])))}
                    for p in PERSONAS]}
        if not apply:
            return {'created': False, 'planned_accounts': len(PERSONAS),
                    'planned_records': sum(len(p['actions']) for p in PERSONAS)}
        password = os.environ.get('FITNESS_DEMO_PASSWORD', '')
        validate_password(password)
        now = s.now_ms()
        clock = [now]
        hashed = password_hash(password)
        output = []
        with db.transaction() as c, patch('fitness_app.records.get_action', catalogue.get):
            view = SeedTransaction(c)
            store = SessionStore(view, lambda: clock[0])
            info = TrainingInfoStore(store, lambda: clock[0])
            app = SimpleNamespace(config={'CLOCK': lambda: clock[0]},
                                  extensions={'database': view, 'session_store': store})
            app.extensions['retests'] = Retests(store)
            tasks = CoachRetests(app)
            nutrition = NutritionProfiles(app)
            for pi, persona in enumerate(PERSONAS):
                email = validate_email(persona['email'], registration=True)
                uid = stable_id(persona['seed_key'])
                auth = stable_id(persona['seed_key'] + '/seed-auth')
                clock[0] = now
                c.execute(s.users.insert().values(
                    id=uid, username_display=email, username_key=email, password_hash=hashed))
                c.execute(s.user_profiles.insert().values(
                    user_id=uid, nickname=persona['nickname'], avatar_id=persona['avatar']))
                c.execute(s.auth_sessions.insert().values(
                    id=auth, user_id=uid, token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                    created_at_ms=now - 14 * 86400000, expires_at_ms=now + 86400000))
                info.write(uid, auth, {'expected_version': 0, **persona['profile']}, s.new_id())
                nutrition.write(uid, auth, {'expected_version': 0, 'inputs': dict(
                    subject='self', adult=True, age_years=persona['profile']['age_years'],
                    weight=persona['profile']['weight_kg'], weight_unit='kg',
                    height=persona['profile']['height_cm'], height_unit='cm',
                    formula_sex=persona['sex'], activity_category='moderate',
                    exercise_status='regular',
                    goal='muscle_gain' if pi == 1 else 'general', special_context='none_declared',
                )})
                records = [create_record(store, info, clock, uid, auth, persona, i, pi, now, catalogue)
                           for i in range(len(persona['actions']))]
                clock[0] = now
                task_count = 0
                if pi == 2:
                    for origin_index, target_index in [(1, 8), (10, None)]:
                        options = app.extensions['retests'].options(uid, records[origin_index], auth)['options']
                        if not options:
                            raise ValueError('复测来源没有可用的同项提示')
                        tid = stable_id(email + '/retest/' + str(origin_index))
                        tasks.save(uid, auth, tid, {'origin': options[0]['origin'], 'confirmed': True})
                        if target_index is not None:
                            tasks.bind(uid, auth, tid, {'target_record_id': records[target_index],
                                'expected_version': 0, 'same_trainee': True, 'comparable_conditions': True})
                        task_count += 1
                c.execute(update(s.auth_sessions).where(s.auth_sessions.c.id == auth).values(revoked_at_ms=now))
                output.append(dict(email=email, nickname=persona['nickname'], records=len(records),
                                   retest_tasks=task_count, latest_record_id=records[-1]))
            apply_screenshot_distribution(c, [stable_id(p['seed_key']) for p in PERSONAS])
            if c.exec_driver_sql('PRAGMA foreign_key_check').first():
                raise ValueError('外键检查失败，整批回滚')
        return {'created': True, 'accounts': output}
    finally:
        db.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(seed(args.database, args.apply), ensure_ascii=False, indent=2))
