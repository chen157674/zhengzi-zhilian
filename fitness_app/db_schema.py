"""正式schema13：计划、逐组执行与练习扩展；旧库升级必须显式授权。"""
from __future__ import annotations

import time
import uuid
from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, LargeBinary, ForeignKey,
                        ForeignKeyConstraint, CheckConstraint as Check, UniqueConstraint as Unique, Index)

metadata = MetaData()
SCHEMA_VERSION = 13
TABLE_VERSIONS = {'posture_observations': 2, 'user_profiles': 3,
                  'training_profiles': 6, 'training_notes': 6,
                  'ai_settings': 7, 'ai_system_policy': 7, 'ai_requests': 7,
                  'video_analyses': 8, 'video_segments': 8, 'video_revisions': 8,
                  'skeleton_tracks':9,'skeleton_chunks':9,'stability_runs':10,'stability_windows':10,'report_exports':11,'nutrition_profiles':12,'coach_saved_retests':12}
SESSION_STATES = ("ready", "countdown", "running", "paused", "waiting", "resting", "ending",
                  "completed", "ended_early", "interrupted", "failed")
JOB_STATES = ("queued", "validating", "running", "cancelling", "completed", "cancelled", "interrupted", "failed")


def now_ms():
    return time.time_ns() // 1_000_000


def new_id():
    return str(uuid.uuid4())


def ident(name="id", fk=None, nullable=False):
    args = [ForeignKey(fk, ondelete="RESTRICT")] if fk else []
    return Column(name, String(36), *args, primary_key=name == "id", nullable=nullable,
                  default=new_id if name == "id" else None)


def number(name, default=0, nullable=False):
    return Column(name, Integer, nullable=nullable, server_default=str(default) if default is not None else None)


def stamp(name="created_at_ms", nullable=False):
    return Column(name, Integer, nullable=nullable, default=None if nullable else now_ms)


def enum(name, values, default=None):
    quoted = ",".join("'" + value + "'" for value in values)
    return (Column(name, Text, nullable=False, server_default=default), Check(f"{name} IN ({quoted})"))


def json_field(name, kind="object", nullable=False, default=None):
    return (Column(name, Text, nullable=nullable, default=default),
            Check(f"{name} IS NULL OR (json_valid({name}) AND json_type({name})='{kind}')"))


app_meta = Table("app_meta", metadata,
    Column("id", Integer, primary_key=True), Column("project_id", Text, nullable=False),
    number("schema_version", 1), stamp("initialized_at_ms", True), stamp(), Check("id=1"))

users = Table("users", metadata,
    ident(), Column("username_display", Text, nullable=False), Column("username_key", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False), *enum("role", ("user", "admin"), "user"),
    number("enabled", 1), number("must_change_password"), number("version"), number("auth_version"),
    Column("temporary_password_hash", Text), stamp("temporary_expires_at_ms", True), stamp(), stamp("updated_at_ms"),
    Check("enabled IN (0,1) AND must_change_password IN (0,1)"),
    Check("version>=0 AND auth_version>=0"),
    Check("(temporary_password_hash IS NULL)=(temporary_expires_at_ms IS NULL)"))

user_profiles = Table('user_profiles', metadata,
    Column('user_id',String(36),ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),
    Column('nickname',Text,nullable=False),Column('avatar_id',Text,nullable=False),Column('avatar_key',Text),number('version'),stamp(),stamp('updated_at_ms'),
    Check('length(nickname) BETWEEN 2 AND 24'),Check('version>=0'),
    Check("avatar_id IN ("+','.join("'avatar-%02d'"%i for i in range(1,25))+")"))

auth_sessions = Table("auth_sessions", metadata,
    ident(), ident("user_id", "users.id"), Column("token_hash", Text, nullable=False, unique=True),
    number("auth_version"), stamp(), stamp("expires_at_ms"), stamp("revoked_at_ms", True),
    number("temporary_login"), Check("temporary_login IN (0,1) AND auth_version>=0"),
    Check("expires_at_ms>=created_at_ms"), Unique("id", "user_id"))
Index("ix_auth_user_expiry", auth_sessions.c.user_id, auth_sessions.c.expires_at_ms)

action_capabilities = Table("action_capabilities", metadata,
    Column("action_id", Text, primary_key=True), Column("capability_version", Text, primary_key=True),
    *json_field("definition_json"), number("definition_schema_version", 1),
    Column("definition_hash", String(64), nullable=False), stamp(),
    Check("definition_schema_version>0 AND length(definition_hash)=64"))

analysis_sessions = Table("analysis_sessions", metadata,
    ident(), ident("user_id", "users.id"), ident("owner_auth_session_id"),
    Column("action_id", Text, nullable=False), Column("capability_version", Text, nullable=False),
    *enum("source", ("camera", "video", "demo")), *enum("mode", ("free", "target")),
    *json_field("config_json"), number("config_schema_version", 1), *enum("status", SESSION_STATES, "ready"),
    number("state_version"), number("last_event_seq"), number("active_ms"), number("hold_ms"),
    stamp(), stamp("started_at_ms", True), stamp("ended_at_ms", True), Column("end_reason", Text),
    Column("source_sha256", String(64)), number("source_bytes", None, True), number("source_duration_ms", None, True),
    *enum("resume_from", ("running", "resting"), "running"), number("rest_remaining_ms"), number("completed_sets"),
    ForeignKeyConstraint(["action_id", "capability_version"], ["action_capabilities.action_id", "action_capabilities.capability_version"], ondelete="RESTRICT"),
    ForeignKeyConstraint(["owner_auth_session_id", "user_id"], ["auth_sessions.id", "auth_sessions.user_id"], ondelete="RESTRICT"),
    Unique("id", "user_id"), Check("state_version>=0 AND last_event_seq>=0 AND active_ms>=0 AND hold_ms>=0"),
    Check("rest_remaining_ms>=0 AND completed_sets>=0 AND config_schema_version>0"),
    Check("source!='camera' OR (source_sha256 IS NULL AND source_bytes IS NULL AND source_duration_ms IS NULL)"))
Index("ix_sessions_user_time", analysis_sessions.c.user_id, analysis_sessions.c.created_at_ms, analysis_sessions.c.id)
Index("ix_sessions_user_state", analysis_sessions.c.user_id, analysis_sessions.c.status)

session_sets = Table("session_sets", metadata,
    ident(), Column("session_id", String(36), ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False),
    number("set_index", 1), number("target_value", None, True), Column("target_unit", Text), number("rest_seconds", None, True),
    number("left_count"), number("right_count"), number("total_count"), number("hold_ms"),
    *enum("status", ("pending", "active", "completed", "ended_early"), "pending"),
    Unique("session_id", "set_index"), Unique("id", "session_id"),
    Check("set_index BETWEEN 1 AND 100"),
    Check("left_count>=0 AND right_count>=0 AND total_count>=0 AND hold_ms>=0"),
    Check("(target_value IS NULL AND target_unit IS NULL AND rest_seconds IS NULL) OR "
          "(target_value IS NOT NULL AND target_unit IS NOT NULL AND rest_seconds IS NOT NULL AND "
          "rest_seconds BETWEEN 0 AND 3600 AND ((target_unit IN ('reps','per_side_reps') AND target_value BETWEEN 1 AND 1000) "
          "OR (target_unit='seconds' AND target_value BETWEEN 1 AND 3600)))"))

action_events = Table("action_events", metadata,
    ident(), Column("session_id", String(36), ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False),
    ident("set_id"), number("event_seq", None), *enum("side", ("left", "right", "none"), "none"),
    number("start_ms", None), number("end_ms", None), Column("algorithm_version", Text, nullable=False),
    Column("feature_schema_version", Text, nullable=False), *json_field("assessment_json", "array", default="[]"),
    number("assessment_schema_version", 1),
    ForeignKeyConstraint(["set_id", "session_id"], ["session_sets.id", "session_sets.session_id"], ondelete="CASCADE"),
    Unique("session_id", "event_seq"), Check("event_seq>0 AND start_ms>=0 AND end_ms>start_ms AND assessment_schema_version>0"))
Index("ix_events_session_end", action_events.c.session_id, action_events.c.end_ms)

observation_intervals = Table("observation_intervals", metadata,
    ident(), Column("session_id", String(36), ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False),
    ident("set_id"), *enum("kind", ("hold", "issue", "unassessable")), Column("item_code", Text),
    number("start_ms", None), number("end_ms", None), Column("algorithm_version", Text, nullable=False), Column("reason", Text),
    ForeignKeyConstraint(["set_id", "session_id"], ["session_sets.id", "session_sets.session_id"], ondelete="CASCADE"),
    Check("start_ms>=0 AND end_ms>start_ms"),
    Check("(kind!='hold' OR item_code IS NULL) AND (kind!='issue' OR item_code IS NOT NULL)"))
Index("ix_intervals_session", observation_intervals.c.session_id, observation_intervals.c.kind,
      observation_intervals.c.item_code, observation_intervals.c.start_ms)

posture_observations = Table("posture_observations", metadata,
    ident(), Column("session_id", String(36), ForeignKey("analysis_sessions.id", ondelete="CASCADE"), nullable=False),
    ident("set_id"), Column("code", Text, nullable=False), *enum("side", ("left", "right", "none"), "none"),
    number("start_ms", None), number("end_ms", None), Column("rule_version", Text, nullable=False),
    *json_field("data_json"),
    ForeignKeyConstraint(["set_id", "session_id"], ["session_sets.id", "session_sets.session_id"], ondelete="CASCADE"),
    Unique("session_id", "set_id", "code", "side", "start_ms", "rule_version"),
    Check("start_ms>=0 AND end_ms>start_ms"))
Index("ix_posture_session_start", posture_observations.c.session_id, posture_observations.c.start_ms)

media_inputs = Table("media_inputs", metadata,
    ident(), ident("user_id", "users.id"), *enum("status", ("validating", "ready", "rejected", "consumed", "cleaned"), "validating"),
    Column("sha256", String(64)), number("bytes", None, True), number("duration_ms", None, True),
    *json_field("encoding_json", nullable=True), Column("temporary_relative_path", Text), stamp(), stamp("expires_at_ms"),
    Unique("id", "user_id"), Check("bytes IS NULL OR bytes>=0"), Check("duration_ms IS NULL OR duration_ms>=0"))

experiments = Table("experiments", metadata,
    ident(), ident("owner_id", "users.id"), ident("owner_auth_session_id"),
    *json_field("config_json"), number("config_schema_version", 1), *enum("status", JOB_STATES, "queued"),
    *enum("result_completeness", ("none", "partial", "full"), "none"),
    *json_field("results_json", nullable=True), Column("truth_status", Text, nullable=False), stamp(), stamp("ended_at_ms", True),
    ForeignKeyConstraint(["owner_auth_session_id", "owner_id"], ["auth_sessions.id", "auth_sessions.user_id"], ondelete="RESTRICT"),
    Unique("id", "owner_id"), Check("config_schema_version>0"))

jobs = Table("jobs", metadata,
    ident(), ident("owner_id", "users.id"), *enum("kind", ("video", "experiment")),
    ident("session_id", nullable=True), ident("experiment_id", nullable=True), ident("input_id", "media_inputs.id", True),
    *enum("status", JOB_STATES, "queued"), number("processed_ms", None, True), number("total_ms", None, True),
    Column("error_code", Text), number("worker_epoch"), number("state_version"), stamp(),
    stamp("started_at_ms", True), stamp("ended_at_ms", True),
    ForeignKeyConstraint(["session_id", "owner_id"], ["analysis_sessions.id", "analysis_sessions.user_id"], ondelete="CASCADE"),
    ForeignKeyConstraint(["experiment_id", "owner_id"], ["experiments.id", "experiments.owner_id"], ondelete="CASCADE"),
    ForeignKeyConstraint(["input_id", "owner_id"], ["media_inputs.id", "media_inputs.user_id"], ondelete="RESTRICT"),
    Check("(kind='video' AND session_id IS NOT NULL AND experiment_id IS NULL) OR "
          "(kind='experiment' AND session_id IS NULL AND experiment_id IS NOT NULL)"),
    Check("worker_epoch>=0 AND state_version>=0"),
    Check("processed_ms IS NULL OR processed_ms>=0"), Check("total_ms IS NULL OR total_ms>=0"))
Index("ix_jobs_owner_status", jobs.c.owner_id, jobs.c.status, jobs.c.created_at_ms)

admin_operations = Table("admin_operations", metadata,
    ident(), ident("actor_user_id", "users.id"), ident("target_user_id", "users.id"),
    Column("operation", Text, nullable=False), Column("result", Text, nullable=False),
    Column("command_id", String(36), nullable=False, unique=True), stamp(), Column("error_code", Text),
    Column("source", Text, nullable=False, server_default="web"))

command_receipts = Table("command_receipts", metadata,
    Column("command_id", String(36), primary_key=True), ident("actor_user_id", "users.id"),
    Column("resource_type", Text, nullable=False), Column("resource_id", String(36)),
    Column("request_digest", String(64), nullable=False), *enum("status", ("pending", "succeeded", "failed"), "pending"),
    *json_field("result_json", nullable=True), stamp(), stamp("ended_at_ms", True),
    Check("length(request_digest)=64"))

# 关系／范围由数据库实际执行，不能仅依赖调用者自觉。
receipt_saved_index=Index('ix_receipts_resource_saved',command_receipts.c.actor_user_id,
                         command_receipts.c.resource_id,command_receipts.c.ended_at_ms,command_receipts.c.resource_type)

training_profiles = Table('training_profiles', metadata,
    Column('user_id',String(36),ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),
    *json_field('data_json',default=lambda:'{}'),number('version'),stamp(),stamp('updated_at_ms'),
    Check('version>=0 AND length(data_json)<=8192'))

training_notes = Table('training_notes', metadata,
    Column('session_id',String(36),primary_key=True),Column('user_id',String(36),nullable=False),
    *json_field('data_json',default=lambda:'{}'),number('version'),stamp(),stamp('updated_at_ms'),
    ForeignKeyConstraint(['session_id','user_id'],['analysis_sessions.id','analysis_sessions.user_id'],ondelete='CASCADE'),
    Check('version>=0 AND length(data_json)<=8192'))


ai_settings = Table('ai_settings', metadata,
    Column('user_id',String(36),ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),
    *enum('source',('system','personal'),'system'),Column('model',Text,nullable=False,server_default='deepseek-flash'),
    Column('key_ciphertext',Text),Column('key_suffix',Text),number('history_reference',1),number('version'),
    stamp(),stamp('updated_at_ms'),stamp('verified_at_ms',True),
    Check('version>=0 AND history_reference IN (0,1)'),Check('(key_ciphertext IS NULL)=(key_suffix IS NULL)'))

ai_system_policy = Table('ai_system_policy', metadata,
    Column('id',Integer,primary_key=True),number('enabled'),Column('key_ciphertext',Text),Column('key_suffix',Text),
    number('budget_micro_cny'),number('spent_micro_cny'),number('reserved_micro_cny'),number('per_user_daily_requests'),
    number('max_output_tokens',1800),number('version'),stamp(),stamp('updated_at_ms'),
    Check('id=1 AND enabled IN (0,1) AND version>=0'),
    Check('budget_micro_cny>=0 AND spent_micro_cny>=0 AND reserved_micro_cny>=0 AND per_user_daily_requests>=0'),
    Check('max_output_tokens BETWEEN 128 AND 8192'),Check('(key_ciphertext IS NULL)=(key_suffix IS NULL)'))

ai_requests = Table('ai_requests', metadata,
    ident(),ident('user_id','users.id'),ident('auth_id'),Column('kind',Text,nullable=False),
    *enum('source',('system','personal')),*enum('status',('queued','running','succeeded','failed','cancelled','uncertain'),'queued'),
    Column('model',Text,nullable=False),Column('request_digest',String(64),nullable=False),*json_field('context_json'),
    *json_field('result_json',nullable=True),*json_field('usage_json',nullable=True),Column('error_code',Text),
    number('reserved_micro_cny'),number('estimated_micro_cny',None,True),number('settings_version'),number('policy_version'),
    number('max_output_tokens',1800),Column('pricing_version',Text,nullable=False),number('cancel_requested'),number('state_version'),
    stamp(),stamp('started_at_ms',True),stamp('ended_at_ms',True),
    ForeignKeyConstraint(['auth_id','user_id'],['auth_sessions.id','auth_sessions.user_id'],ondelete='RESTRICT'),
    Check('reserved_micro_cny>=0 AND (estimated_micro_cny IS NULL OR estimated_micro_cny>=0)'),
    Check('settings_version>=0 AND policy_version>=0 AND state_version>=0 AND length(request_digest)=64'),
    Check('cancel_requested IN (0,1) AND max_output_tokens BETWEEN 128 AND 8192'))
Index('ix_ai_requests_owner_time',ai_requests.c.user_id,ai_requests.c.created_at_ms)

video_analyses = Table('video_analyses',metadata,
    ident(),ident('user_id','users.id'),ident('owner_auth_session_id'),ident('input_id'),
    Column('source_sha256',String(64),nullable=False),number('source_bytes',None),number('duration_ms',None,True),
    number('source_width',None),number('source_height',None),*enum('mode',('auto','manual'),'auto'),
    Column('manual_action_id',Text),*json_field('config_json'),*enum('status',JOB_STATES,'queued'),
    *enum('phase',('queued','recognizing','posture','finalizing','completed'),'queued'),
    *enum('result_completeness',('none','partial','full'),'none'),number('processed_ms'),number('state_version'),
    Column('recognition_model_version',Text),Column('error_code',Text),number('cancel_requested'),number('cleanup_pending'),
    stamp(),stamp('started_at_ms',True),stamp('ended_at_ms',True),Unique('id','user_id'),
    ForeignKeyConstraint(['owner_auth_session_id','user_id'],['auth_sessions.id','auth_sessions.user_id'],ondelete='RESTRICT'),
    ForeignKeyConstraint(['input_id','user_id'],['media_inputs.id','media_inputs.user_id'],ondelete='RESTRICT'),
    Check("length(source_sha256)=64 AND source_bytes>0 AND source_width>0 AND source_height>0"),
    Check("duration_ms IS NULL OR duration_ms BETWEEN 1 AND 1800000"),
    Check("(mode='auto' AND manual_action_id IS NULL) OR (mode='manual' AND manual_action_id IS NOT NULL)"),
    Check('processed_ms>=0 AND state_version>=0 AND cancel_requested IN (0,1) AND cleanup_pending IN (0,1)'))
Index('ix_video_analyses_owner_time',video_analyses.c.user_id,video_analyses.c.created_at_ms)

video_segments = Table('video_segments',metadata,
    ident(),ident('user_id','users.id'),ident('analysis_id'),number('segment_index',None),
    number('start_ms',None),number('end_ms',None),Column('action_id',Text),*enum('kind',('action','unknown')),
    Column('reason',Text),Column('recognition_model_version',Text),*json_field('metadata_json'),number('state_version'),stamp(),
    Unique('id','user_id'),Unique('analysis_id','segment_index'),
    ForeignKeyConstraint(['analysis_id','user_id'],['video_analyses.id','video_analyses.user_id'],ondelete='CASCADE'),
    Check('segment_index>0 AND start_ms>=0 AND end_ms>start_ms AND state_version>=0'),
    Check("(kind='action' AND action_id IS NOT NULL) OR (kind='unknown' AND action_id IS NULL)"))

video_revisions = Table('video_revisions',metadata,
    ident(),ident('user_id','users.id'),ident('segment_id'),ident('input_id'),ident('owner_auth_session_id'),
    number('revision_index',None),*enum('origin',('auto','manual')),Column('action_id',Text,nullable=False),
    ident('session_id',nullable=True),ident('previous_revision_id',nullable=True),*enum('status',JOB_STATES,'queued'),
    number('is_current'),number('state_version'),Column('error_code',Text),*json_field('config_json'),
    number('cleanup_pending'),
    stamp(),stamp('started_at_ms',True),stamp('ended_at_ms',True),Unique('segment_id','revision_index'),Unique('session_id'),
    ForeignKeyConstraint(['segment_id','user_id'],['video_segments.id','video_segments.user_id'],ondelete='CASCADE'),
    ForeignKeyConstraint(['session_id','user_id'],['analysis_sessions.id','analysis_sessions.user_id'],ondelete='CASCADE'),
    ForeignKeyConstraint(['input_id','user_id'],['media_inputs.id','media_inputs.user_id'],ondelete='RESTRICT'),
    ForeignKeyConstraint(['owner_auth_session_id','user_id'],['auth_sessions.id','auth_sessions.user_id'],ondelete='RESTRICT'),
    Check('revision_index>0 AND is_current IN (0,1) AND state_version>=0 AND cleanup_pending IN (0,1)'),
    Check("is_current=0 OR (status='completed' AND session_id IS NOT NULL)"))
Index('ix_video_revision_current',video_revisions.c.segment_id,unique=True,sqlite_where=video_revisions.c.is_current==1)


skeleton_tracks = Table('skeleton_tracks',metadata,
    ident(),ident('user_id'),*enum('status',('active','complete','limited','save_failed','interrupted','deleted','expired'),'active'),
    *json_field('metadata_json'),number('data_bytes'),number('frame_count'),number('visible_frame_count'),number('chunk_count'),number('last_ms',-1),
    number('state_version'),Column('reason',Text),stamp(),stamp('expires_at_ms'),
    ForeignKeyConstraint(['id','user_id'],['analysis_sessions.id','analysis_sessions.user_id'],ondelete='CASCADE'),
    Check('data_bytes>=0 AND frame_count>=0 AND visible_frame_count BETWEEN 0 AND frame_count AND chunk_count>=0 AND last_ms>=-1 AND state_version>=0'),
    Check('expires_at_ms>created_at_ms'))
Index('ix_skeleton_owner_expiry',skeleton_tracks.c.user_id,skeleton_tracks.c.expires_at_ms)
skeleton_chunks = Table('skeleton_chunks',metadata,
    ident(),ident('track_id'),number('chunk_index',None),number('first_ms',None),number('last_ms',None),
    Column('payload',LargeBinary,nullable=False),Column('sha256',String(64),nullable=False),
    ForeignKeyConstraint(['track_id'],['skeleton_tracks.id'],ondelete='CASCADE'),
    Check('chunk_index>=0 AND first_ms>=0 AND last_ms>=first_ms'),Check('length(payload)<=131072 AND length(sha256)=64'))
Index('ix_skeleton_chunk_order',skeleton_chunks.c.track_id,skeleton_chunks.c.chunk_index,unique=True)


stability_runs=Table('stability_runs',metadata,
    ident(),ident('user_id'),Column('policy_version',Text,nullable=False),
    *enum('status',('active','complete','interrupted','partial','limited'),'active'),number('window_count'),number('state_version'),
    Column('reason',Text),stamp(),
    ForeignKeyConstraint(['id','user_id'],['analysis_sessions.id','analysis_sessions.user_id'],ondelete='CASCADE'),
    Check('window_count BETWEEN 0 AND 1000 AND state_version>=0'))
stability_windows=Table('stability_windows',metadata,
    ident(),ident('session_id'),number('window_index',None),*json_field('data_json'),Column('sha256',String(64),nullable=False),
    ForeignKeyConstraint(['session_id'],['stability_runs.id'],ondelete='CASCADE'),Check('window_index>=0 AND length(sha256)=64'),
    Check('length(data_json)<=32768'))
Index('ix_stability_window_order',stability_windows.c.session_id,stability_windows.c.window_index,unique=True)


report_exports=Table('report_exports',metadata,
    ident(),ident('user_id','users.id'),ident('auth_id'),Column('fingerprint',String(64),nullable=False),
    *enum('status',('queued','running','ready','failed','expired','invalidated','deleted'),'queued'),
    *json_field('options_json'),*json_field('dependencies_json'),*json_field('snapshot_json',nullable=True),
    Column('pdf_blob',LargeBinary),Column('pdf_sha256',String(64)),number('data_bytes'),number('state_version'),
    Column('error_code',Text),stamp(),stamp('expires_at_ms'),stamp('generated_at_ms',True),
    ForeignKeyConstraint(['auth_id','user_id'],['auth_sessions.id','auth_sessions.user_id'],ondelete='RESTRICT'),
    Check('length(fingerprint)=64 AND state_version>=0 AND data_bytes BETWEEN 0 AND 10485760'),
    Check("(status='ready' AND pdf_blob IS NOT NULL AND pdf_sha256 IS NOT NULL AND data_bytes>0 AND data_bytes=length(pdf_blob) AND length(pdf_sha256)=64) OR (status!='ready' AND pdf_blob IS NULL AND data_bytes=0)"),
    Check('expires_at_ms>created_at_ms'))
Index('ix_exports_owner_created',report_exports.c.user_id,report_exports.c.created_at_ms)
Index('ix_exports_owner_fingerprint',report_exports.c.user_id,report_exports.c.fingerprint)


nutrition_profiles=Table('nutrition_profiles',metadata,
    Column('user_id',String(36),ForeignKey('users.id',ondelete='CASCADE'),primary_key=True),*json_field('data_json'),number('version'),stamp('updated_at_ms'),
    Check('version>=0 AND length(data_json)<=4096'))

coach_saved_retests=Table('coach_saved_retests',metadata,
    ident(),ident('user_id','users.id'),Column('origin_json',Text,nullable=False),
    Column('target_record_id',String(36)),number('target_version',None,True),number('state_version'),stamp(),stamp('expires_at_ms'),
    Check('length(origin_json)<=4096 AND state_version>=0 AND expires_at_ms>created_at_ms'))
Index('ix_coach_retest_owner_expiry',coach_saved_retests.c.user_id,coach_saved_retests.c.expires_at_ms)
coach_retest_identity_index=Index('ix_coach_retest_owner_identity',coach_saved_retests.c.id,coach_saved_retests.c.user_id,unique=True)


training_plans=Table('training_plans',metadata,
    ident(),ident('user_id','users.id'),Column('title',Text,nullable=False),Column('goal',Text,nullable=False),
    *enum('status',('draft','active','paused','archived'),'draft'),Column('timezone',Text,nullable=False,server_default='Asia/Shanghai'),
    Column('start_date',String(10),nullable=False),number('weeks',None),Column('current_revision_id',String(36)),
    number('state_version'),stamp(),stamp('updated_at_ms'),stamp('activated_at_ms',True),
    Unique('id','user_id'),Check('length(title) BETWEEN 1 AND 60 AND weeks BETWEEN 1 AND 8 AND state_version>=0'))
Index('ix_training_one_current',training_plans.c.user_id,unique=True,sqlite_where=training_plans.c.status.in_(['active','paused']))
training_plan_revisions=Table('training_plan_revisions',metadata,
    ident(),ident('user_id','users.id'),ident('plan_id'),number('revision_number',None),*json_field('constraints_json'),
    *json_field('sessions_json','array'),*json_field('knowledge_refs_json','array'),Column('source',Text,nullable=False),
    number('source_profile_version',None,True),Column('template_version',Text,nullable=False),stamp(),
    ForeignKeyConstraint(['plan_id','user_id'],['training_plans.id','training_plans.user_id'],ondelete='CASCADE'),
    Unique('id','user_id'),Unique('plan_id','revision_number'),Check('revision_number>=1'))
training_plan_sessions=Table('training_plan_sessions',metadata,
    ident(),ident('user_id','users.id'),ident('plan_id'),ident('revision_id'),Column('scheduled_date',String(10),nullable=False),
    Column('title',Text,nullable=False),number('estimated_minutes'),*enum('status',('planned','skipped','cancelled'),'planned'),
    number('schedule_version'),Column('rescheduled_from',String(10)),Column('superseded_by',String(36)),stamp(),
    ForeignKeyConstraint(['plan_id','user_id'],['training_plans.id','training_plans.user_id'],ondelete='CASCADE'),
    ForeignKeyConstraint(['revision_id','user_id'],['training_plan_revisions.id','training_plan_revisions.user_id'],ondelete='CASCADE'),
    Unique('id','user_id'),Check('schedule_version>=0 AND estimated_minutes>=0'))
Index('ix_training_session_date',training_plan_sessions.c.plan_id,training_plan_sessions.c.scheduled_date,unique=True,sqlite_where=training_plan_sessions.c.status!='cancelled')
training_plan_items=Table('training_plan_items',metadata,
    ident(),ident('user_id','users.id'),ident('session_id'),number('position',None),Column('action_id',Text,nullable=False),
    Column('variant',Text),number('target_sets',None),number('target_reps',None,True),number('target_duration_ms',None,True),
    number('rest_seconds'),*json_field('optional_load_json',nullable=True),Column('focus_task_id',String(36)),
    ForeignKeyConstraint(['session_id','user_id'],['training_plan_sessions.id','training_plan_sessions.user_id'],ondelete='CASCADE'),
    Unique('id','user_id'),Unique('session_id','position'),
    Check('target_sets BETWEEN 1 AND 10 AND rest_seconds BETWEEN 0 AND 600'),
    Check('(target_reps BETWEEN 1 AND 100 AND target_duration_ms IS NULL) OR (target_reps IS NULL AND target_duration_ms BETWEEN 1000 AND 600000)'))
training_workouts=Table('training_workouts',metadata,
    ident(),ident('user_id','users.id'),ident('plan_session_id'),ident('plan_revision_id'),
    *enum('status',('ready','running','paused','completed','ended_early','voided'),'ready'),number('state_version'),
    *json_field('target_snapshot_json'),*json_field('completion_json'),stamp(),stamp('actual_started_at_ms',True),stamp('actual_ended_at_ms',True),
    number('elapsed_active_ms'),stamp('active_since_ms',True),Column('confirmation_source',Text),
    ForeignKeyConstraint(['plan_session_id','user_id'],['training_plan_sessions.id','training_plan_sessions.user_id'],ondelete='CASCADE'),
    ForeignKeyConstraint(['plan_revision_id','user_id'],['training_plan_revisions.id','training_plan_revisions.user_id'],ondelete='CASCADE'),
    Unique('plan_session_id'),Unique('id','user_id'),Check('state_version>=0 AND elapsed_active_ms>=0'))
Index('ix_training_one_running',training_workouts.c.user_id,unique=True,sqlite_where=training_workouts.c.status=='running')
training_workout_sets=Table('training_workout_sets',metadata,
    ident(),ident('user_id','users.id'),ident('workout_id'),ident('item_id'),number('set_index',None),
    number('actual_reps',None,True),number('actual_duration_ms',None,True),*json_field('load_json',nullable=True),
    *enum('status',('done','skipped'),'done'),*enum('source',('manual','record'),'manual'),stamp('saved_at_ms'),
    ForeignKeyConstraint(['workout_id','user_id'],['training_workouts.id','training_workouts.user_id'],ondelete='CASCADE'),
    ForeignKeyConstraint(['item_id','user_id'],['training_plan_items.id','training_plan_items.user_id'],ondelete='CASCADE'),
    Unique('workout_id','item_id','set_index'),Check('set_index BETWEEN 1 AND 10'),
    Check("(status='skipped' AND actual_reps IS NULL AND actual_duration_ms IS NULL) OR (status='done' AND ((actual_reps>0 AND actual_duration_ms IS NULL) OR (actual_reps IS NULL AND actual_duration_ms>0)))"))
training_record_allocations=Table('training_record_allocations',metadata,
    ident(),ident('user_id','users.id'),ident('workout_id'),ident('item_id'),Column('record_id',String(36),nullable=False),
    number('record_version',None),*json_field('allocations_json','array'),Column('source_sha256',String(64)),
    Column('unit',Text,nullable=False),number('allocated_quantity',None),number('valid',1),stamp(),
    ForeignKeyConstraint(['workout_id','user_id'],['training_workouts.id','training_workouts.user_id'],ondelete='CASCADE'),
    ForeignKeyConstraint(['item_id','user_id'],['training_plan_items.id','training_plan_items.user_id'],ondelete='CASCADE'),
    Check('valid IN (0,1) AND record_version>=0 AND allocated_quantity>=0'))
Index('ix_training_allocation_source',training_record_allocations.c.user_id,training_record_allocations.c.record_id)
training_schedule_events=Table('training_schedule_events',metadata,
    ident(),ident('user_id','users.id'),ident('plan_id'),Column('session_id',String(36)),Column('command_id',String(36),nullable=False),
    Column('event_type',Text,nullable=False),Column('from_date',String(10)),Column('to_date',String(10)),Column('reason',Text),number('plan_version'),stamp(),
    ForeignKeyConstraint(['plan_id','user_id'],['training_plans.id','training_plans.user_id'],ondelete='CASCADE'),
    Unique('command_id','session_id','event_type'))
coach_practice_details=Table('coach_practice_details',metadata,
    Column('retest_task_id',String(36),primary_key=True),
    ident('user_id','users.id'),number('state_version'),*enum('status',('saved','practicing','awaiting_retest','completed','dismissed'),'saved'),
    *json_field('steps_json','array'),*json_field('knowledge_refs_json','array'),Column('linked_workout_id',String(36)),stamp(),stamp('updated_at_ms'),
    ForeignKeyConstraint(['retest_task_id','user_id'],['coach_saved_retests.id','coach_saved_retests.user_id'],ondelete='CASCADE'),
    ForeignKeyConstraint(['linked_workout_id','user_id'],['training_workouts.id','training_workouts.user_id'],ondelete='RESTRICT'),Check('state_version>=0'))
for name in ('training_plans','training_plan_revisions','training_plan_sessions','training_plan_items','training_workouts','training_workout_sets','training_record_allocations','training_schedule_events','coach_practice_details'):
    TABLE_VERSIONS[name]=13


for table in metadata.tables.values():
    for column in table.columns:
        if column.name == "id" and isinstance(column.type,String):
            table.append_constraint(Check("length(id)=36"))
        if column.name.endswith("_at_ms"):
            table.append_constraint(Check(f"{column.name} IS NULL OR {column.name}>=0"))
        if isinstance(column.type, Integer):
            table.append_constraint(Check(f"{column.name} IS NULL OR typeof({column.name})='integer'"))


def create_schema(connection):
    metadata.create_all(connection)
    connection.exec_driver_sql("""CREATE TRIGGER capabilities_immutable_update BEFORE UPDATE ON action_capabilities
        BEGIN SELECT RAISE(ABORT,'capability versions are immutable'); END""")
    connection.exec_driver_sql("""CREATE TRIGGER capabilities_immutable_delete BEFORE DELETE ON action_capabilities
        BEGIN SELECT RAISE(ABORT,'capability versions are immutable'); END""")
    for operation in ("INSERT", "UPDATE"):
        connection.exec_driver_sql(f"""CREATE TRIGGER intervals_no_overlap_{operation.lower()}
            BEFORE {operation} ON observation_intervals
            WHEN EXISTS(SELECT 1 FROM observation_intervals AS old
                WHERE old.id != NEW.id AND old.session_id=NEW.session_id AND old.set_id=NEW.set_id
                  AND old.kind=NEW.kind AND old.item_code IS NEW.item_code
                  AND old.start_ms<NEW.end_ms AND old.end_ms>NEW.start_ms)
            BEGIN SELECT RAISE(ABORT,'overlapping observation interval'); END""")
