"""单实例回环服务。停止凭本机实例文件中的随机令牌，不结束其他进程。"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
import webbrowser

from .paths import PROJECT_ID, ROOT, runtime_path

_owned_children: dict[int, subprocess.Popen] = {}


def read_instance() -> dict | None:
    path = runtime_path() / "instance.json"
    if not path.exists():
        return None
    record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("project_id") != PROJECT_ID:
        raise RuntimeError("实例文件身份不符，不覆盖或停止未知服务")
    return record


def health(record: dict) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{int(record['port'])}/api/v1/health", timeout=1) as response:
            result = json.load(response)["data"]
        return result if result.get("project_id") == PROJECT_ID and result.get("instance_id") == record["instance_id"] else None
    except (OSError, ValueError, KeyError):
        return None


def serve(port: int = 8765) -> None:
    import msvcrt
    from flask import jsonify, request
    from waitress import create_server
    from .app import create_app

    runtime = runtime_path()
    lock_path = runtime / "service.lock"
    with lock_path.open("a+b") as lock:
        if lock_path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise RuntimeError("本项目已有正式服务实例，拒绝重复启动") from error
        stopped = threading.Event()
        requested_instance=os.environ.get('FITNESS_LAUNCH_ID')
        instance_id = str(uuid.UUID(requested_instance)) if requested_instance else str(uuid.uuid4())
        stop_token = secrets.token_urlsafe(32)
        app = create_app({"INSTANCE_ID": instance_id})
        @app.post("/_local/stop")
        def stop():
            if not hmac.compare_digest(request.headers.get("X-Local-Stop", ""), stop_token):
                return jsonify(error={"code": "FORBIDDEN", "message": "需要本机实例控制凭据"}), 403
            for callback in app.extensions.get("stop_hooks", []):
                callback()
            stopped.set()
            return jsonify(data={"stopping": True})

        for callback in app.extensions.get('start_hooks',[]):
            callback()

        options={"host":"127.0.0.1","port":port,"threads":4,"map":{}}
        if app.config.get('MOBILE_PUBLIC_ORIGIN'):
            options.update(trusted_proxy='127.0.0.1',trusted_proxy_count=1,
                           trusted_proxy_headers={'x-forwarded-proto'},clear_untrusted_proxy_headers=True)
        try:
            server = create_server(app, **options)
        except OSError:
            options['port']=0;server = create_server(app, **options)
        record = {"project_id": PROJECT_ID, "instance_id": instance_id, "pid": os.getpid(),
                  "port": int(server.effective_port), "stop_token": stop_token}
        instance_file = runtime / "instance.json"
        pending = runtime / "instance.pending"
        pending.write_text(json.dumps(record), encoding="utf-8")
        pending.replace(instance_file)
        worker = threading.Thread(target=server.run, name="fitness-http", daemon=True)
        worker.start()
        try:
            while not stopped.wait(0.2):
                if not worker.is_alive():
                    raise RuntimeError("本机 HTTP 服务意外退出")
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
            worker.join(timeout=2)
            if instance_file.exists() and json.loads(instance_file.read_text(encoding="utf-8")).get("instance_id") == instance_id:
                instance_file.unlink()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def launch(port: int = 8765, open_browser: bool = True) -> dict:
    existing = read_instance()
    if existing and health(existing):
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{existing['port']}")
        return {"port": existing["port"], "pid": existing["pid"], "reused": True}
    runtime = runtime_path()
    launch_id=str(uuid.uuid4());child_environment=os.environ.copy();child_environment['FITNESS_LAUNCH_ID']=launch_id
    with (runtime / "server.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, "-B", "-m", "fitness_app", "serve", "--port", str(port)],
                                   cwd=ROOT, env=child_environment, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        _owned_children[process.pid] = process
    for _ in range(100):
        record = read_instance()
        if record and health(record):
            # Windows虚拟环境启动器可能另起实际Python进程，不能用PID是否相同判定复用。
            reused=record['instance_id']!=launch_id
            if not reused:
                _owned_children.pop(process.pid,None);_owned_children[record['pid']]=process
            if open_browser:
                webbrowser.open(f"http://127.0.0.1:{record['port']}")
            return {"port": record["port"], "pid": record["pid"], "reused": reused}
        if process.poll() is not None:
            raise RuntimeError("服务启动失败，请查看项目内运行日志")
        time.sleep(0.1)
    raise RuntimeError("请查看项目运行日志和启动状态")


def stop_instance() -> dict:
    record = read_instance()
    if not record:
        return {"stopped": True, "was_running": False}
    if not health(record):
        raise RuntimeError("请核对当前实例状态后停止服务")
    request = urllib.request.Request(f"http://127.0.0.1:{record['port']}/_local/stop", method="POST",
                                     headers={"X-Local-Stop": record["stop_token"]})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            json.load(response)
    except urllib.error.HTTPError as error:
        try:message=json.load(error).get('error',{}).get('message','正在停止服务')
        except (ValueError,AttributeError):message='请查看训练保存状态后停止服务'
        raise RuntimeError(message) from error
    for _ in range(60):
        if not health(record) and not (runtime_path() / "instance.json").exists():
            owned = _owned_children.pop(record["pid"], None)
            if owned is not None:
                owned.wait(timeout=5)
            return {"stopped": True, "was_running": True}
        time.sleep(0.1)
    raise RuntimeError("请查看服务停止状态")


def main():
    parser = argparse.ArgumentParser(description="健身助手正式本机入口")
    parser.add_argument("command", nargs="?", choices=["start", "serve"], default="start")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--init-admin", action="store_true")
    parser.add_argument("--recover-admin", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口不合法")
    if args.init_admin or args.recover_admin:
        if args.init_admin and args.recover_admin:
            parser.error("请选择初始化或恢复其中一种操作")
        import getpass
        from sqlalchemy import select
        from .database import Database
        from .db_schema import users
        from .auth import initialize_admin,recover_admin
        existing=read_instance()
        if existing and health(existing):
            raise RuntimeError("请先用 start.cmd --stop 停止本项目服务，再执行本机管理员维护")
        db=Database(os.environ.get("FITNESS_DATABASE_PATH",str(ROOT/"data/app.sqlite3")))
        try:
            if args.recover_admin:
                db.inspect_identity()
                with db.transaction(False) as connection:
                    names=connection.execute(select(users.c.username_display).where(users.c.role=="admin",users.c.enabled==1)).scalars().all()
                if not names:
                    raise RuntimeError("请先配置有效管理员账号")
                print("可恢复的有效管理员："+"、".join(names))
            username=input("管理员用户名：")
            password=getpass.getpass("新密码（12–128 字符，隐藏输入）：")
            confirmation=getpass.getpass("再次输入新密码：")
            if password!=confirmation:
                raise ValueError("两次密码不一致，未保存")
            if args.recover_admin and input("将撤销全部旧登录与临时密码，输入 YES 确认：")!="YES":
                print("已取消，未更改账号")
                return
            result=(initialize_admin if args.init_admin else recover_admin)(db,username,password)
            print("本机管理员维护完成："+result["username"])
        finally:
            db.dispose()
    elif args.command == "serve":
        serve(args.port)
    elif args.stop:
        print(json.dumps(stop_instance()))
    elif args.status:
        record = read_instance()
        print(json.dumps({"running": bool(record and health(record)), "port": record.get("port") if record else None}))
    else:
        print(json.dumps(launch(args.port, not args.no_browser)))
