"""为指定现有数据库建立独立评审体验账号；整批事务、重复运行不覆盖本人后续操作。"""
from __future__ import annotations

import argparse
import datetime as dt
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
from sqlalchemy import func, select, update
from fitness_app import db_schema as s
from fitness_app.auth import password_hash, validate_password
from fitness_app.database import Database, json_text
from fitness_app.diet_journal import DietJournal
from fitness_app.nutrition_profiles import NutritionProfiles
from fitness_app.records import SessionStore
from fitness_app.training_info import TrainingInfoStore
from fitness_app.training_plans import TrainingPlans, ZONE
from fitness_app.training_progress import TrainingProgress
from fitness_app.training_workouts import TrainingWorkouts
from scripts import seed_screenshot_users as scenarios

DATASET = "review-account-1"
USERNAME = "review_judge"
NICKNAME = "评审体验"
PROVENANCE = {"seed_dataset": DATASET, "synthetic_data": True, "model_detected": False}
PROFILE = {"age_years": 22, "height_cm": 175, "weight_kg": 69,
           "experience": "beginner", "goals": ["training_habit", "movement_quality"],
           "available_minutes": 35, "equipment": ["bodyweight", "dumbbell"]}


def stable_id(value):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, DATASET + "/" + value))


def stamp(day, hour=19, minute=0):
    return int(dt.datetime.combine(day, dt.time(hour, minute), ZONE).timestamp() * 1000)


def counts(c, uid):
    tables = (s.analysis_sessions, s.training_plans, s.training_plan_sessions,
              s.training_workouts, s.training_workout_sets, s.nutrition_journal, s.personal_memories)
    return {table.name: c.scalar(select(func.count()).select_from(table).where(table.c.user_id == uid))
            for table in tables}


def make_plan(start):
    sessions = []
    for week in range(4):
        for offset in (1, 3, 6):
            day = start + dt.timedelta(days=week * 7 + offset)
            suffix = day.isoformat()
            items = []
            for action, reps, duration, load in [
                ("bodyweight_squat", 10, None, None),
                ("standing_dumbbell_curl", 10, None, {"value": 5, "unit": "kg", "basis": "per_hand"}),
                ("forearm_plank", None, 30000, None),
            ]:
                items.append({"client_id": suffix + "/" + action, "action_id": action, "variant": None,
                              "target_sets": 3 if action != "forearm_plank" else 2,
                              "target_reps": reps, "target_duration_ms": duration,
                              "rest_seconds": 60, "load": load, "focus_task_id": None})
            sessions.append({"client_id": suffix, "date": suffix, "day_kind": "training",
                             "title": "全身基础训练", "estimated_minutes": 35, "items": items})
    return {"title": "每周三练 · 基础力量", "goal": "training_habit", "start_date": start.isoformat(),
            "weeks": 4, "timezone": "Asia/Shanghai",
            "constraints": {"experience": "beginner", "training_weekdays": [2, 4, 7],
                            "minutes_per_session": 35, "equipment": ["bodyweight", "dumbbell"],
                            "preferences": "先把动作练稳，周二、周四和周日晚上训练。"},
            "sessions": sessions}


def seed_workouts(app, uid, auth, plan, today, clock):
    service = app.extensions["training_workouts"]
    notes = ["今天精神不错，深蹲先保持现在的次数。",
             "弯举最后一组有点吃力，重量先不加。",
             "临时有事，做完前两项就结束了。",
             "组间休息充分一些，整体节奏比上次顺。",
             "平板支撑第二组需要集中注意力，继续练稳定。"]
    past = [row for row in plan["sessions"] if row["scheduled_date"] < today.isoformat()]
    for index, row in enumerate(past):
        day = dt.date.fromisoformat(row["scheduled_date"])
        clock[0] = stamp(day, 19)
        workout = service.create(uid, auth, {"plan_session_id": row["id"], "session_expected_version": 0},
                                 stable_id("workout/" + day.isoformat()))
        wid = workout["id"]
        with service.db.transaction() as c:
            c.execute(update(s.training_workouts).where(s.training_workouts.c.id == wid).values(created_at_ms=clock[0]))
        workout = service.command(uid, auth, wid, {"expected_version": 0, "type": "start"}, s.new_id())
        partial = index == 2
        for item_index, item in enumerate(workout["target_snapshot"]["items"]):
            if partial and item_index == 2:
                break
            for group in range(1, item["target_sets"] + 1):
                clock[0] += 180000
                workout = service.set(uid, auth, wid, item["id"], group,
                    {"expected_version": workout["state_version"], "source": "manual", "status": "done",
                     "actual_reps": item["target_reps"], "actual_duration_ms": item["target_duration_ms"],
                     "load": item["load"]}, s.new_id())
        clock[0] = stamp(day, 19, 25 if partial else 35 + index % 3)
        workout = service.command(uid, auth, wid,
            {"expected_version": workout["state_version"], "type": "end_early" if partial else "complete",
             "confirmed": True}, s.new_id())
        service.save_note(uid, auth, wid, {"expected_version": workout["state_version"], "note": notes[index % len(notes)]}, s.new_id())
        with service.db.transaction() as c:
            row_data = c.scalar(select(s.training_workouts.c.completion_json).where(s.training_workouts.c.id == wid))
            c.execute(update(s.training_workouts).where(s.training_workouts.c.id == wid).values(
                completion_json=json_text({**json.loads(row_data), **PROVENANCE})))


BREAKFAST = [
    [("燕麦", "staple", "一小碗"), ("牛奶", "protein", "250毫升"), ("鸡蛋", "protein", "1个")],
    [("全麦面包", "staple", "2片"), ("豆浆", "protein", "1杯"), ("苹果", "fruit", "半个")],
    [("玉米", "staple", "1根"), ("鸡蛋", "protein", "1个"), ("酸奶", "protein", "1杯")],
]
LUNCH = [
    [("米饭", "staple", "1碗"), ("鸡肉", "protein", "一掌心"), ("青菜", "vegetable", "一大份")],
    [("杂粮饭", "staple", "1碗"), ("牛肉", "protein", "一份"), ("西兰花", "vegetable", "一份")],
    [("面条", "staple", "1碗"), ("鸡蛋", "protein", "1个"), ("菠菜", "vegetable", "一份")],
]
DINNER = [
    [("米饭", "staple", "小半碗"), ("豆腐", "protein", "一份"), ("番茄", "vegetable", "一份")],
    [("红薯", "staple", "1个"), ("鱼", "protein", "一掌心"), ("白菜", "vegetable", "一份")],
    [("米饭", "staple", "1碗"), ("虾", "protein", "一份"), ("生菜", "vegetable", "一份")],
]


def seed_meals(app, uid, auth, today, now, clock):
    diet = DietJournal(app)
    for age in range(9, -1, -1):
        day = today - dt.timedelta(days=age)
        for meal, hour, choices in [("breakfast", 8, BREAKFAST), ("lunch", 12, LUNCH), ("dinner", 20, DINNER)]:
            # 留有忘记记录的餐次；当天只预置当前时间之前的内容。
            if (age, meal) in {(8, "breakfast"), (6, "dinner"), (3, "lunch")} or stamp(day, hour) > now:
                continue
            clock[0] = stamp(day, hour)
            foods = [{"name": name, "group": group, "amount": amount}
                     for name, group, amount in choices[age % len(choices)]]
            saved = diet.save(uid, auth,
                {"consumed_date": day.isoformat(), "meal_type": meal, "foods": foods,
                 "note": "食堂吃的，蔬菜多选了一份。" if meal == "lunch" and age % 3 == 0 else ""},
                stable_id(f"meal/{day}/{meal}"))
            with diet.db.transaction() as c:
                archived = saved.get("analysis") or {}
                c.execute(update(s.nutrition_journal).where(s.nutrition_journal.c.id == saved["id"]).values(
                    analysis_json=json_text({**archived, **PROVENANCE})))
    clock[0] = now
    diet.remember(uid, auth, stable_id("preferences"), "我喜欢吃鸡蛋和豆腐。我喜欢喝牛奶。我不喜欢吃芹菜。")


def seed(database, apply=False, password=None, now=None):
    path = project_path(database)
    if not path.is_file():
        raise ValueError("目标数据库不存在，不创建替代数据库")
    now = s.now_ms() if now is None else now
    today = dt.datetime.fromtimestamp(now / 1000, ZONE).date()
    start = today - dt.timedelta(days=today.weekday() + 7)
    uid, auth, manifest = (stable_id(x) for x in ("user", "seed-auth", "manifest"))
    db = Database(path)
    try:
        with db.transaction(False) as c:
            if c.scalar(select(s.app_meta.c.schema_version)) != s.SCHEMA_VERSION:
                raise ValueError("目标数据库版本不匹配，不迁移原库")
            existing = c.execute(select(s.users).where(s.users.c.username_key == USERNAME)).mappings().first()
            if existing:
                receipt = c.execute(select(s.command_receipts).where(s.command_receipts.c.command_id == manifest)).mappings().first()
                if existing["id"] != uid or not receipt or receipt["actor_user_id"] != uid:
                    raise ValueError("账号名已被其他用户占用，未覆盖")
                return {"created": False, "username": USERNAME, "nickname": NICKNAME,
                        "password_changed": False, "counts": counts(c, uid)}
            catalogue = {}
            for row in c.execute(select(s.action_capabilities).order_by(
                    s.action_capabilities.c.created_at_ms, s.action_capabilities.c.capability_version)).mappings():
                catalogue[row["action_id"]] = json.loads(row["definition_json"])
            if not {"bodyweight_squat", "forearm_plank", "standing_dumbbell_curl"}.issubset(catalogue):
                raise ValueError("目标数据库缺少所需动作能力")
        if not apply:
            return {"created": False, "username": USERNAME, "nickname": NICKNAME,
                    "plan_start": start.isoformat(), "plan_weeks": 4, "training_days_per_week": 3}
        password = password or ("Fit-" + secrets.token_urlsafe(12))
        validate_password(password)
        clock = [stamp(start, 9)]
        with db.transaction() as c, patch("fitness_app.records.get_action", catalogue.get):
            view = scenarios.SeedTransaction(c)
            store = SessionStore(view, lambda: clock[0])
            info = TrainingInfoStore(store, lambda: clock[0])
            app = SimpleNamespace(config={"CLOCK": lambda: clock[0], "SECRET_KEY": secrets.token_hex(32)},
                                  extensions={"database": view, "session_store": store, "training_info": info})
            app.extensions["training_plans"] = plans = TrainingPlans(app)
            app.extensions["training_progress"] = TrainingProgress(app)
            app.extensions["training_workouts"] = TrainingWorkouts(app)
            c.execute(s.users.insert().values(id=uid, username_display=USERNAME, username_key=USERNAME,
                password_hash=password_hash(password), role="user", created_at_ms=clock[0], updated_at_ms=clock[0]))
            c.execute(s.user_profiles.insert().values(user_id=uid, nickname=NICKNAME, avatar_id="avatar-05",
                                                     created_at_ms=clock[0], updated_at_ms=clock[0]))
            c.execute(s.auth_sessions.insert().values(id=auth, user_id=uid,
                token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                created_at_ms=clock[0], expires_at_ms=now + 86400000))
            info.write(uid, auth, {"expected_version": 0, **PROFILE}, s.new_id())
            NutritionProfiles(app).write(uid, auth, {"expected_version": 0, "inputs": {
                "subject": "self", "adult": True, "age_years": 22, "weight": 69, "weight_unit": "kg",
                "height": 175, "height_unit": "cm", "formula_sex": "male", "activity_category": "moderate",
                "exercise_status": "regular", "goal": "general", "special_context": "none_declared"}})
            plan = plans.create(uid, auth, make_plan(start), stable_id("plan"))
            plan = plans.command(uid, auth, plan["id"], "activate", {"expected_version": 0, "confirmed": True}, stable_id("activate"))
            for table in (s.training_plans, s.training_plan_revisions, s.training_plan_sessions):
                c.execute(update(table).where(table.c.user_id == uid).values(created_at_ms=clock[0]))
            seed_workouts(app, uid, auth, plan, today, clock)
            # 复用既有合成场景生成器，显式保留非模型检测的来源信息，不复制真实用户记录。
            past_days = [dt.date.fromisoformat(row["scheduled_date"]) for row in plan["sessions"]
                         if row["scheduled_date"] < today.isoformat()]
            actions = ["bodyweight_squat", "standing_dumbbell_curl", "forearm_plank"]
            record_days = [day for day in past_days for _ in range(2)]
            persona = {"actions": [actions[i % 3] for i in range(len(record_days))]}
            with patch.object(scenarios, "DATASET", DATASET), patch.object(scenarios, "PROVENANCE", PROVENANCE), \
                    patch.object(scenarios, "DAYS", [(today - day).days for day in record_days]):
                for index in range(len(record_days)):
                    scenarios.create_record(store, info, clock, uid, auth, persona, index, 0,
                                            stamp(today, 19, 35 + index % 2 * 8), catalogue)
            seed_meals(app, uid, auth, today, now, clock)
            clock[0] = now
            c.execute(update(s.auth_sessions).where(s.auth_sessions.c.id == auth).values(revoked_at_ms=now))
            result = {"username": USERNAME, "nickname": NICKNAME, "role": "user",
                      "counts": counts(c, uid), "plan_start": start.isoformat(), **PROVENANCE}
            c.execute(s.command_receipts.insert().values(command_id=manifest, actor_user_id=uid,
                resource_type="review_account.seed", resource_id=uid, request_digest=hashlib.sha256(DATASET.encode()).hexdigest(),
                status="succeeded", result_json=json_text(result), ended_at_ms=now))
            if c.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise ValueError("关联完整性检查失败，整批回滚")
        return {"created": True, **result, "password": password}
    finally:
        db.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(seed(args.database, args.apply, os.environ.get("FITNESS_REVIEW_PASSWORD")),
                     ensure_ascii=False, indent=2))
