"""仅在项目专属测试库重建登记过的旧版形状，不操作正式库或猜历史DDL。"""
from contextlib import closing
import sqlite3
from fitness_app import db_schema as s
from fitness_app.paths import ROOT

def legacy_shape(database,version):
    if type(version) is not int or not 1<=version<s.SCHEMA_VERSION:raise ValueError('旧测试版本无效')
    target=database.path.resolve();base=(ROOT/'.tmp/tests').resolve()
    if not base.is_relative_to(ROOT.resolve()) or not target.is_relative_to(base):raise ValueError('旧形状夹具只允许项目内临时测试库')
    database.dispose()
    with closing(sqlite3.connect(target)) as c,c:
        c.execute('BEGIN IMMEDIATE')
        for table in reversed(s.metadata.sorted_tables):
            if s.TABLE_VERSIONS.get(table.name,1)>version:c.execute('DROP TABLE "'+table.name+'"')
        if version==3:c.execute('ALTER TABLE user_profiles DROP COLUMN avatar_key')
        if version<5:c.execute('DROP INDEX IF EXISTS ix_receipts_resource_saved')
        if version<13:c.execute('DROP INDEX IF EXISTS ix_coach_retest_owner_identity')
        c.execute('UPDATE app_meta SET schema_version=? WHERE id=1',(version,))

def rows_snapshot(database,tables=None):
    """在内存记录旧版实际列/行，升级后按旧列比较，忽略新增空列。"""
    target=database.path.resolve()
    if not target.is_relative_to((ROOT/'.tmp/tests').resolve()):raise ValueError('快照只允许项目测试库')
    result={}
    quote=lambda value:'"'+value.replace('"','""')+'"'
    with closing(sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)) as c:
        for name in tables or [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name!='app_meta'")]:
            columns=tables[name]['columns'] if isinstance(tables,dict) else [r[1] for r in c.execute('PRAGMA table_info('+quote(name)+')')]
            result[name]={'columns':columns,'rows':list(c.execute('SELECT '+','.join(quote(v) for v in columns)+' FROM '+quote(name)+' ORDER BY rowid'))}
    return result
