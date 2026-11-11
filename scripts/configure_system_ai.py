"""项目本地系统密钥导入；隐藏输入，不创建新库、不改变预算或启用状态。"""
import argparse
import getpass
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import configure_environment, project_path
from fitness_app.database import Database
from fitness_app.ai_secrets import SecretVault
from fitness_app.ai_settings import AISettingsStore, money
from fitness_app.records import integer
from fitness_app.deepseek_client import DeepSeekClient, ProviderError, validate_key
from fitness_app import db_schema as s


def provision(database, key):
    key = validate_key(key)
    path = project_path(database)
    if not path.is_file():
        raise ValueError('目标库不存在，拒绝新建替代库')
    db = Database(path)
    try:
        db.inspect_identity()
        store = AISettingsStore(SimpleNamespace(db=db), SecretVault(path.parent/'private/ai-master.key'))
        with db.transaction() as connection:
            old = store.policy(connection)
            unchanged = bool(old['key_ciphertext'] and store.vault.open('system', old['key_ciphertext']) == key)
            if not unchanged:
                values = {'key_ciphertext': store._seal(connection, 'system', key), 'key_suffix': key[-4:],
                          'version': old['version']+1, 'updated_at_ms': s.now_ms()}
                if old['_exists']:
                    connection.execute(s.ai_system_policy.update().where(s.ai_system_policy.c.id == 1).values(**values))
                else:
                    connection.execute(s.ai_system_policy.insert().values(id=1, enabled=0, **values))
            result = store.admin_snapshot(connection)
            # 验证实际密文，不打印或回传后四位/真实密钥。
            current = store.policy(connection)
            if store.vault.open('system', current['key_ciphertext']) != key:
                raise ValueError('密钥回读核验失败，事务回滚')
            return {k: v for k, v in result.items() if k != 'system_key_mask'} | {'changed': not unchanged}
    finally:
        db.engine.dispose()


def configure_limits(database, budget_yuan, per_user_daily_requests, enabled=False):
    path=project_path(database)
    if not path.is_file():raise ValueError('目标库不存在')
    budget=money(budget_yuan,'budget_yuan')
    daily=integer(per_user_daily_requests,'per_user_daily_requests',0,10000)
    if type(enabled) is not bool:raise ValueError('启用状态必须明确')
    db=Database(path)
    try:
        db.inspect_identity()
        store=AISettingsStore(SimpleNamespace(db=db),SecretVault(path.parent/'private/ai-master.key'))
        with db.transaction() as c:
            old=store.policy(c)
            if not old['_exists'] or not old['key_ciphertext']:raise ValueError('请先导入系统密钥')
            if budget and budget<old['spent_micro_cny']+old['reserved_micro_cny']:raise ValueError('预算不能低于已消费及预留')
            if enabled:
                validate_key(store.vault.open('system',old['key_ciphertext']))
            if (old['budget_micro_cny'],old['per_user_daily_requests'],bool(old['enabled']))!=(budget,daily,enabled):
                c.execute(s.ai_system_policy.update().where(s.ai_system_policy.c.id==1).values(
                    budget_micro_cny=budget,per_user_daily_requests=daily,enabled=int(enabled),
                    version=old['version']+1,updated_at_ms=s.now_ms()))
            return {k:v for k,v in store.admin_snapshot(c).items() if k!='system_key_mask'}
    finally:db.engine.dispose()


def main():
    configure_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--verify', action='store_true', help='仅GET官方模型列表，不进行生成或发送图片')
    parser.add_argument('--key-stdin', action='store_true', help='通过受控管道输入；交互默认使用隐藏输入')
    parser.add_argument('--limits-only', action='store_true', help='仅应用用户明确批准的额度，不重新输入密钥')
    parser.add_argument('--budget-yuan')
    parser.add_argument('--daily', type=int)
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    if args.limits_only:
        if args.budget_yuan is None or args.daily is None:parser.error('额度模式必须指定总预算和每日次数')
        if args.verify or args.key_stdin:parser.error('额度模式不接受密钥或验证选项')
        result=configure_limits(args.database,args.budget_yuan,args.daily,args.enable)
        print(json.dumps(result,ensure_ascii=False))
        return
    if args.budget_yuan is not None or args.daily is not None or args.enable:parser.error('修改额度需明确选择--limits-only')
    key = sys.stdin.readline().rstrip('\r\n') if args.key_stdin else getpass.getpass('系统API密钥（隐藏输入）: ')
    try:
        result = provision(args.database, key)
        if args.verify:
            try:
                result['connection'] = DeepSeekClient(timeout=25).test(key)
            except ProviderError as error:
                result['connection'] = {'connection_verified': False, 'error_code': error.code}
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        # 底层数据库异常可能包含参数，绝不直接输出异常正文或traceback。
        print(json.dumps({'saved': False, 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        key = None


if __name__ == '__main__':
    main()
