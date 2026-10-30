"""带身份检查和显式事务的项目 SQLite；未知库绝不自动建表。"""
from __future__ import annotations

from contextlib import contextmanager, closing
import hashlib
import json
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.pool import QueuePool
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from .paths import PROJECT_ID, project_path, runtime_path
from .db_schema import SCHEMA_VERSION, TABLE_VERSIONS, metadata, app_meta, command_receipts, create_schema, now_ms


class StorageError(RuntimeError):
    pass


class ConflictError(ValueError):
    def __init__(self,message,code="VERSION_CONFLICT"):
        super().__init__(message)
        self.code=code


def json_text(value, kind=dict):
    if not isinstance(value, kind):
        raise ValueError("JSON 类型不匹配")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def command_digest(resource_type,resource_id,payload):
    return hashlib.sha256(json_text({"type":resource_type,"id":resource_id,"payload":payload}).encode()).hexdigest()


@contextmanager
def maintenance_lock(path: Path, exact=False):
    import msvcrt
    lock_path = path if exact else path.with_name(path.name + ".maintenance.lock")
    with lock_path.open("a+b") as handle:
        if lock_path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise StorageError("数据库维护正被占用，未执行迁移") from error
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class Database:
    def __init__(self, path: Path | str):
        self.path = project_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine("sqlite:///" + str(self.path), poolclass=QueuePool, pool_size=5, max_overflow=0, pool_timeout=1,
                                    connect_args={"timeout": 1, "check_same_thread": False})

        @event.listens_for(self.engine, "connect")
        def configure(dbapi, _):
            dbapi.isolation_level = None
            dbapi.execute("PRAGMA foreign_keys=ON")
            dbapi.execute("PRAGMA busy_timeout=1000")

    @contextmanager
    def transaction(self, write=True):
        # SQLite连接复用保留PRAGMA与事务边界；池耗尽按既有保存故障冻结，不能等默认30秒。
        try:
            leased = self.engine.connect()
        except PoolTimeoutError as error:
            raise StorageError("数据库连接繁忙，未开始本次事务") from error
        with leased as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def inspect_identity(self, allow_legacy=False, connection=None):
        if connection is not None:
            identity=self._inspect_identity(connection.exec_driver_sql,allow_legacy)
            if connection.exec_driver_sql('PRAGMA foreign_key_check').first() is not None:
                raise StorageError('目标数据库关联数据校验失败，未提交迁移')
            return identity
        if not self.path.exists():return None
        try:
            with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as reader:
                reader.execute('BEGIN')
                return self._inspect_identity(reader.execute,allow_legacy)
        except sqlite3.Error as error:
            raise StorageError('数据库身份无法核实或文件损坏；未清空／重建') from error

    def _inspect_identity(self,execute,allow_legacy):
        row=execute('SELECT project_id,schema_version FROM app_meta WHERE id=1').fetchone()
        tables={r[0] for r in execute("SELECT name FROM sqlite_master WHERE type='table'")}
        legacy = allow_legacy and row in {(PROJECT_ID,v) for v in range(1,SCHEMA_VERSION)}
        if row != (PROJECT_ID, SCHEMA_VERSION) and not legacy:
            raise StorageError("数据库项目身份或 schema 版本不兼容，拒绝写入")
        additions={name for name,version in TABLE_VERSIONS.items() if version>row[1]} if legacy else set()
        required = set(metadata.tables) - additions
        if not required.issubset(tables):
            raise StorageError("正式 schema 缺表，拒绝自动补空表")
        if additions.intersection(tables):
            raise StorageError("旧库包含未登记的新表，拒绝覆盖")
        for name in required:
            columns={r[1] for r in execute(f'PRAGMA table_info("{name}")')}
            expected=set(metadata.tables[name].columns.keys())
            if legacy and row[1]==3 and name=='user_profiles':
                if 'avatar_key' in columns:raise StorageError('旧资料表包含未登记头像字段，拒绝覆盖')
                expected.remove('avatar_key')
            if not expected.issubset(columns):
                raise StorageError("正式 schema 缺字段，拒绝写入或迁移")
        if row[1]>=5:
            columns=[r[2] for r in execute("PRAGMA index_info('ix_receipts_resource_saved')")]
            indices=[r for r in execute("PRAGMA index_list('command_receipts')") if r[1]=='ix_receipts_resource_saved']
            if columns!=['actor_user_id','resource_id','ended_at_ms','resource_type'] or not indices or indices[0][2]:
                raise StorageError('保存回执索引缺失或不兼容，拒绝写入')
        if row[1]>=8:
            columns=[r[2] for r in execute("PRAGMA index_info('ix_video_revision_current')")]
            indices=[r for r in execute("PRAGMA index_list('video_revisions')") if r[1]=='ix_video_revision_current']
            sql=execute("SELECT sql FROM sqlite_master WHERE type='index' AND name='ix_video_revision_current'").fetchone()
            normalized=''.join(sql[0].lower().split()) if sql and sql[0] else ''
            if columns!=['segment_id'] or not indices or not indices[0][2] or not indices[0][4] or not normalized.endswith('whereis_current=1'):
                raise StorageError('片段当前修订唯一索引缺失或不兼容，拒绝写入')
        if row[1]>=9:
            columns=[r[2] for r in execute("PRAGMA index_info('ix_skeleton_chunk_order')")]
            indices=[r for r in execute("PRAGMA index_list('skeleton_chunks')") if r[1]=='ix_skeleton_chunk_order']
            chunks=list(execute("PRAGMA foreign_key_list('skeleton_chunks')"))
            tracks=list(execute("PRAGMA foreign_key_list('skeleton_tracks')"))
            owner_groups={r[0] for r in tracks if r[2]=='analysis_sessions' and r[3]=='id' and r[4]=='id' and r[6]=='CASCADE'}
            if columns!=['track_id','chunk_index'] or not indices or not indices[0][2] or not any(r[2]=='skeleton_tracks' and r[3]=='track_id' and r[4]=='id' and r[6]=='CASCADE' for r in chunks) or not any(r[0] in owner_groups and r[3]=='user_id' and r[4]=='user_id' and r[6]=='CASCADE' for r in tracks):
                raise StorageError('骨架唯一顺序索引或删除级联不兼容，拒绝写入')
        if row[1]>=10:
            columns=[r[2] for r in execute("PRAGMA index_info('ix_stability_window_order')")]
            indices=[r for r in execute("PRAGMA index_list('stability_windows')") if r[1]=='ix_stability_window_order']
            children=list(execute("PRAGMA foreign_key_list('stability_windows')"));parents=list(execute("PRAGMA foreign_key_list('stability_runs')"))
            owner_groups={r[0] for r in parents if r[2]=='analysis_sessions' and r[3]=='id' and r[4]=='id' and r[6]=='CASCADE'}
            if columns!=['session_id','window_index'] or not indices or not indices[0][2] or not any(r[2]=='stability_runs' and r[3]=='session_id' and r[4]=='id' and r[6]=='CASCADE' for r in children) or not any(r[0] in owner_groups and r[3]=='user_id' and r[4]=='user_id' and r[6]=='CASCADE' for r in parents):
                raise StorageError('运动摘要唯一顺序索引或删除级联不兼容，拒绝写入')
        if row[1]>=11:
            for index,expected in [('ix_exports_owner_created',['user_id','created_at_ms']),('ix_exports_owner_fingerprint',['user_id','fingerprint'])]:
                columns=[r[2] for r in execute(f"PRAGMA index_info('{index}')")];indices=[r for r in execute("PRAGMA index_list('report_exports')") if r[1]==index]
                if columns!=expected or not indices or indices[0][2]:raise StorageError('报告查询索引不兼容，拒绝写入')
            refs=list(execute("PRAGMA foreign_key_list('report_exports')"));groups={r[0] for r in refs if r[2]=='auth_sessions' and r[3]=='auth_id' and r[4]=='id' and r[6]=='RESTRICT'}
            if not any(r[0] in groups and r[3]=='user_id' and r[4]=='user_id' and r[6]=='RESTRICT' for r in refs):raise StorageError('报告所有人约束不兼容，拒绝写入')
        return {"project_id": row[0], "schema_version": row[1]}

    def initialize(self):
        with maintenance_lock(self.path):
            if self.path.exists():
                identity = self.inspect_identity(allow_legacy=True)
                if identity['schema_version'] < SCHEMA_VERSION:
                    from .db_schema import posture_observations,user_profiles,users,receipt_saved_index
                    from .profile_assets import random_profile,AVATAR_IDS
                    try:
                        with self.transaction() as connection:
                            if identity['schema_version']==1:posture_observations.create(connection)
                            if identity['schema_version']<=2:
                                user_profiles.create(connection)
                                for uid in connection.execute(select(users.c.id)).scalars():
                                    connection.execute(user_profiles.insert().values(user_id=uid,**random_profile()))
                            elif identity['schema_version']==3:
                                connection.exec_driver_sql('ALTER TABLE user_profiles ADD COLUMN avatar_key TEXT')
                                for profile in connection.execute(select(user_profiles.c.user_id,user_profiles.c.avatar_id)).mappings():
                                    if profile['avatar_id'] not in AVATAR_IDS:
                                        connection.execute(update(user_profiles).where(user_profiles.c.user_id==profile['user_id'])
                                            .values(avatar_id=AVATAR_IDS[0],version=user_profiles.c.version+1,updated_at_ms=now_ms()))
                            columns=[r[2] for r in connection.exec_driver_sql("PRAGMA index_info('ix_receipts_resource_saved')")]
                            indices=[r for r in connection.exec_driver_sql("PRAGMA index_list('command_receipts')") if r[1]=='ix_receipts_resource_saved']
                            if indices and (columns!=['actor_user_id','resource_id','ended_at_ms','resource_type'] or indices[0][2]):
                                raise StorageError('旧库存在不兼容同名回执索引，拒绝覆盖')
                            receipt_saved_index.create(connection,checkfirst=True)
                            for table in metadata.sorted_tables:
                                if TABLE_VERSIONS.get(table.name,1)>max(identity['schema_version'],3):
                                    table.create(connection)
                            connection.execute(update(app_meta).where(app_meta.c.id == 1).values(schema_version=SCHEMA_VERSION))
                            self.inspect_identity(connection=connection)
                    except Exception as error:
                        raise StorageError("本机数据升级未完成，已回滚；原库保留") from error
                return self.inspect_identity()
            try:
                with self.transaction() as connection:
                    create_schema(connection)
                    connection.execute(app_meta.insert().values(id=1, project_id=PROJECT_ID,
                                                                 schema_version=SCHEMA_VERSION))
                    self.inspect_identity(connection=connection)
                return self.inspect_identity()
            except BaseException:
                self.engine.dispose()
                # 仅移除本次独占维护期间新建且未成功初始化的文件。
                if self.path.exists():
                    self.path.unlink()
                raise

    def maintenance_change(self, callback, target_version=SCHEMA_VERSION):
        """受信迁移回调的原子框架；不向 HTTP 暴露任意代码／路径执行。"""
        with maintenance_lock(runtime_path() / "service.lock", exact=True), maintenance_lock(self.path):
            self.inspect_identity()
            with self.transaction() as connection:
                callback(connection)
                connection.execute(update(app_meta).where(app_meta.c.id == 1).values(schema_version=target_version))
                self.inspect_identity(connection=connection)

    def dispose(self):
        self.engine.dispose()


def compare_and_set(connection, table, row_id, version, changes):
    column = table.c.version if "version" in table.c else table.c.state_version
    result = connection.execute(update(table).where(table.c.id == row_id, column == version)
                                .values(**changes, **{column.name: version+1}))
    if result.rowcount != 1:
        raise ConflictError("状态版本过期或资源不存在")


def idempotent(connection, actor_id, command_id, payload, resource_type, callback, resource_id=None):
    digest = command_digest(resource_type,resource_id,payload)
    previous = connection.execute(select(command_receipts).where(command_receipts.c.command_id == command_id)).mappings().first()
    if previous:
        if previous["actor_user_id"] != actor_id or previous["request_digest"] != digest:
            raise ConflictError("幂等标识与请求不匹配","IDEMPOTENCY_CONFLICT")
        if previous["status"] != "succeeded":
            raise ConflictError("原命令尚无成功结果，请先查询状态","INVALID_STATE")
        return json.loads(previous["result_json"])
    connection.execute(command_receipts.insert().values(command_id=command_id, actor_user_id=actor_id,
        resource_type=resource_type, request_digest=digest, status="pending"))
    result = callback()
    forbidden = {"password", "password_hash", "temporary_password", "temporary_password_hash", "control_token", "token", "cookie", "authorization", "csrf_token", "api_key", "personal_key", "system_key", "key_ciphertext"}
    def check_secrets(value):
        if isinstance(value, dict):
            if forbidden.intersection(str(key).lower() for key in value):
                raise ValueError("秘密不得保存到持久命令回执")
            for child in value.values():
                check_secrets(child)
        elif isinstance(value, list):
            for child in value:
                check_secrets(child)
    if not isinstance(result, dict):
        raise ValueError("命令最小结果必须是对象")
    check_secrets(result)
    connection.execute(update(command_receipts).where(command_receipts.c.command_id == command_id).values(
        resource_id=result.get("id"), status="succeeded", result_json=json_text(result), ended_at_ms=now_ms()))
    return result
