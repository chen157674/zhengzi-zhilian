"""仅供本机、单人开发预览使用。标准库启动器与 SQLite 摘要存储。"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlsplit
import uuid
import webbrowser

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
STATES = {"running", "paused", "ended", "interrupted"}


def validate_snapshot(body):
    if not isinstance(body, dict) or body.get("state") not in STATES:
        raise ValueError("记录状态无效")
    if type(body.get("revision")) is not int or body["revision"] < 1:
        raise ValueError("记录版本无效")
    for key in ("hold", "observed", "duration"):
        value = body.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 86400:
            raise ValueError("记录时长无效")
    for key in ("reps", "intervals"):
        if not isinstance(body.get(key), list) or len(body[key]) > 2000:
            raise ValueError("记录明细无效")
        for item in body[key]:
            if not isinstance(item, dict):
                raise ValueError("记录明细无效")
            start, end = item.get("start"), item.get("end")
            if any(type(x) not in (int,float) or not math.isfinite(x) for x in (start,end)) or not 0 <= start <= end <= 86400:
                raise ValueError("记录时间段无效")
            if item.get("issue") is not None and (not isinstance(item["issue"], str) or len(item["issue"]) > 100):
                raise ValueError("问题描述无效")
    for i, rep in enumerate(body["reps"], 1):
        if rep.get("index") != i:
            raise ValueError("动作序号无效")
    return {k: body[k] for k in ("state", "revision", "hold", "observed", "duration", "reps", "intervals")}


class Store:
    def __init__(self, path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, action TEXT NOT NULL, source TEXT NOT NULL, created TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0, payload TEXT NOT NULL)")
            for ident, payload in conn.execute("SELECT id,payload FROM sessions").fetchall():
                data = json.loads(payload)
                if data["state"] in {"running", "paused"}:
                    data["state"] = "interrupted"
                    conn.execute("UPDATE sessions SET payload=? WHERE id=?", (json.dumps(data),ident))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def create(self, action, source):
        if action not in {"squat", "plank"} or source not in {"camera", "video", "demo"}:
            raise ValueError("不支持的动作或来源")
        ident = str(uuid.uuid4())
        data = {"state":"running", "revision":0, "hold":0, "observed":0, "duration":0, "reps":[], "intervals":[]}
        with self.connect() as conn:
            conn.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?)", (ident,action,source,datetime.now(timezone.utc).isoformat(),0,json.dumps(data)))
        return {"id":ident}

    def update(self, ident, data):
        data = validate_snapshot(data)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT revision,payload FROM sessions WHERE id=?", (ident,)).fetchone()
            if row is None:
                raise KeyError("记录不存在")
            if data["revision"] <= row[0]:
                return {"saved":True, "revision":row[0]}
            if json.loads(row[1])["state"] in {"ended", "interrupted"}:
                raise ValueError("记录已经结束，请创建新的训练")
            conn.execute("UPDATE sessions SET revision=?,payload=? WHERE id=?", (data["revision"],json.dumps(data,ensure_ascii=False),ident))
        return {"saved":True, "revision":data["revision"]}

    def list(self):
        with self.connect() as conn:
            rows = conn.execute("SELECT id,action,source,created,payload FROM sessions ORDER BY created DESC LIMIT 30").fetchall()
        return [{"id":r[0],"action":r[1],"source":r[2],"created":r[3],**json.loads(r[4])} for r in rows]


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".mjs":"text/javascript", ".wasm":"application/wasm"}

    def log_request(self, code="-", size="-"):
        if str(code).isdigit() and int(code) >= 400:
            super().log_request(code, size)

    def allowed(self):
        expected = f"127.0.0.1:{self.server.server_port}"
        if self.headers.get("Host") != expected:
            self.send_error(403, "Local host only")
            return False
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{expected}":
            self.send_error(403, "Same origin only")
            return False
        return True

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; worker-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        super().end_headers()

    def json_response(self, status, data):
        content = json.dumps(data,ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if not self.allowed(): return
        path = urlsplit(self.path).path
        if path == "/api/sessions":
            try: self.json_response(200, self.server.store.list())
            except sqlite3.Error: self.json_response(503,{"error":"记录读取失败，请重试"})
        elif path.startswith("/api/"):
            self.json_response(404,{"error":"接口不存在"})
        else:
            resolved = Path(self.translate_path(self.path)).resolve()
            if not resolved.is_relative_to(WEB): self.send_error(403)
            else: super().do_GET()

    def list_directory(self, path):
        self.send_error(404)
        return None

    def do_HEAD(self):
        if self.allowed(): super().do_HEAD()

    def do_POST(self):
        self.mutate()

    def do_PUT(self):
        self.mutate()

    def mutate(self):
        if not self.allowed(): return
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise ValueError("请求必须为 JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024*1024: raise ValueError("请求大小无效")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict): raise ValueError("请求格式无效")
            path = urlsplit(self.path).path
            if self.command == "POST" and path == "/api/sessions":
                result = self.server.store.create(body.get("action"),body.get("source"))
            elif self.command == "PUT" and path.startswith("/api/sessions/"):
                ident = str(uuid.UUID(path.rsplit("/",1)[1]))
                result = self.server.store.update(ident,body)
            else:
                self.json_response(404,{"error":"接口不存在"}); return
            self.json_response(200,result)
        except KeyError:
            self.json_response(404,{"error":"记录不存在"})
        except (ValueError, UnicodeError) as exc:
            self.json_response(400,{"error":str(exc)[:120]})
        except sqlite3.Error:
            self.json_response(503,{"error":"记录保存失败，请重试；当前页面仍保留结果"})


def make_server(port=8765, db=None):
    server = ThreadingHTTPServer(("127.0.0.1",port),partial(Handler,directory=str(WEB)))
    server.store = Store(db or ROOT / "data" / "sessions.sqlite3")
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--open",action="store_true")
    parser.add_argument("--test-db",type=Path)
    args=parser.parse_args()
    if args.test_db and not args.test_db.resolve().is_relative_to(ROOT):
        parser.error("Test database must stay inside the project")
    if not (WEB / "vendor" / "manifest.json").exists():
        raise SystemExit("Run python -B setup_assets.py first.")
    try: server=make_server(args.port,args.test_db)
    except OSError as exc: raise SystemExit(f"Cannot start local server: {exc}")
    url=f"http://127.0.0.1:{server.server_port}"
    print(f"Fitness preview: {url}",flush=True)
    if args.open: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
