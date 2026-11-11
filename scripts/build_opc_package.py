import sys
sys.stdout.reconfigure(encoding='utf-8')
"""整理可移植 OPC 工程与提交包，不包含个人运行数据。"""
from pathlib import Path
import ast
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.request
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "OPC专项赛"
BUILD = ROOT / ".tmp/opc-completion"
APP = BUILD / "作品工程"
BASE = ROOT / "比赛提交/代码仓库"
PYTHON = ROOT / ".venv/Scripts/python.exe"
IGNORED = {"__pycache__", ".git", ".cache", ".tmp", ".ipynb_checkpoints"}
DENIED = {".env", "auth-secret.key", "instance.json", "service.lock"}


def eligible(p):
    return not (set(p.parts) & IGNORED) and p.name not in DENIED and p.suffix.lower() not in {
        ".pyc", ".sqlite3", ".db", ".log", ".lock", ".bak",
    }


def copytree(src, dst):
    for p in src.rglob("*"):
        if p.is_file() and eligible(p.relative_to(src)):
            target = dst / p.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)


def prepare():
    assert APP.resolve().is_relative_to(ROOT)
    BUILD.mkdir(exist_ok=True)
    APP.mkdir(exist_ok=True)
    for folder in ["data"]:
        copytree(BASE / folder, APP / folder)
    copytree(ROOT / "fitness_app", APP / "fitness_app")
    # 品牌素材已内置于 fitness_app/static/brand 目录
    # 第三方准备脚本来自已有可移植版本，应用主体使用本次当前源码。
    (APP / "scripts").mkdir(exist_ok=True)
    for name in ["environment.ps1", "setup.ps1", "setup_resources.py", "start-review.ps1"]:
        shutil.copy2(BASE / "scripts" / name, APP / "scripts" / name)
    shutil.copy2(BASE / "start.cmd", APP / "start.cmd")
    for p in ROOT.glob("requirements*"):
        if p.is_file():
            shutil.copy2(p, APP / p.name)
    for name in ["资源清单.json", ".env.example", ".gitignore"]:
        shutil.copy2(BASE / name, APP / name)
    for p in (ROOT / "data/models").glob("*"):
        if p.is_file() and eligible(p) and p.suffix in {".json", ".joblib", ".npz"}:
            shutil.copy2(p, APP / "data/models" / p.name)
    # 可选同主体下肢显示复核权重；依赖见requirements.correction.txt。
    shutil.copy2(ROOT / 'data/models/yolo11n-pose.pt', APP / 'data/models/yolo11n-pose.pt')
    paths = APP / "fitness_app/paths.py"
    text = paths.read_text(encoding="utf-8")
    original = 'if str(ROOT).lower() != r"E:\\07_Projects\\05_PersonalProjects\\10_fitness-assistant".lower():'
    assert original in text
    text = text.replace(original, 'if not (ROOT / "fitness_app/app.py").is_file():')
    paths.write_text(text, encoding="utf-8")
    readme = r"""# 知练同行——基于姿态识别与大模型协同的智能健身陪练平台

欢迎使用“知练同行”参赛作品工程。本项目是一套融合轻量级姿态估计与多模态大模型智能推理的 Web 全栈运动健康陪练系统，支持训练视频毫秒级时序分析、骨骼力线动态回放、多模态智能指导与自适应训练成长档案。

## 快速启动指南（Windows 环境）

1. **环境准备**：推荐使用 Python 3.12 运行环境。
2. **初始化依赖与环境**：
   在本项目根目录下打开 PowerShell 终端，执行环境初始化脚本：
   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
   ```
3. **初始化体验账号**：
   首次启动前执行管理账号创建指令：
   ```cmd
   .\start.cmd --init-admin
   ```
   按终端提示输入用户名与密码完成初始化。
4. **启动平台服务**：
   执行启动脚本开启本地 Web 服务：
   ```cmd
   .\start.cmd --port 8765
   ```
   服务就绪后，在现代浏览器（Chrome / Edge）中访问：`http://127.0.0.1:8765`
5. **停止服务**：
   体验完成后，在项目根目录下执行停止脚本：
   ```cmd
   .\start.cmd --stop
   ```

## 核心功能推荐体验路径

1. **视频分析与动作诊断**：进入“视频分析”页面，上传一段训练视频（或选择内置测试样本），系统自动提取关键点、切分动作周期并秒级生成结构化问题卡片。
2. **毫秒级时间戳穿梭回看**：点击问题卡片，视频自动跳转至对应动作片段，同步查看 Canvas 骨骼力线与代偿角度高亮标注。
3. **多模态 AI 智能答疑**：在分析结果详情页向 AI 私教追问动作代偿诱因与改善建议，获取基于运动生物力学的专业通俗化指导。
4. **训练成长档案与单项复测**：点击保存记录，进入“训练档案”查看周期数据变化趋势，并可一键发起“单项针对性复测”，体验全流程自适应复练闭环。

## 资源核验说明

项目内置完整的资源校验工具，可在终端执行如下指令进行基础资源合规性检查：
```powershell
python -B scripts/setup_resources.py --check
```
"""
    readme = readme.replace("3. **初始化体验账号**：\n   首次启动前执行管理账号创建指令：\n   ```cmd\n   .\\start.cmd --init-admin\n   ```\n   按终端提示输入用户名与密码完成初始化。", "3. **首次体验**：双击工程目录中的“启动评审体验.cmd”。浏览器保留正常登录与注册入口。首次点击“注册”，填写邮箱和密码，创建账号后回到登录页即可使用；无需执行初始化命令。")
    readme += "\n交付副本不含个人账号库、API密钥。首次依赖安装需要网络和Python 3.12；启动入口会检查并提示。\n"
    (APP / "README.md").write_text(readme, encoding="utf-8")
    launcher = r'''$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'environment.ps1')
try {
    $venvPython = Join-Path $FitnessProjectRoot '.venv\Scripts\python.exe'
    $ready = $false
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython -B -c "import flask, waitress, flask_login, sqlalchemy, mediapipe" 2>$null
        $ready = $LASTEXITCODE -eq 0
    }
    if (-not $ready) {
        Write-Host '首次启动正在准备运行环境，请保持联网。'
        & (Join-Path $PSScriptRoot 'setup.ps1')
        if (-not $?) { throw '依赖准备未完成，请检查上方提示。' }
    }
    & $venvPython -B (Join-Path $PSScriptRoot 'setup_resources.py') --check
    if ($LASTEXITCODE -ne 0) { throw '作品资源不完整，请重新完整解压参赛包。' }
    $env:FITNESS_BODY_LEAN='1'
    $env:FITNESS_MOVEMENT_GUIDANCE='1'
    & $venvPython -B -m fitness_app --port 8765
    if ($LASTEXITCODE -ne 0) { throw '服务启动未完成，请检查上方提示。' }
} catch {
    Write-Host ('启动失败：' + $_.Exception.Message)
    Read-Host '按回车关闭'
    exit 1
}
'''
    (APP / 'scripts/start-experience.ps1').write_text(launcher, encoding='utf-8-sig')
    (APP / '启动评审体验.cmd').write_text('@echo off\ncd /d "%~dp0"\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\\start-experience.ps1"\n', encoding='utf-8')
    # 保留与实际应用相匹配的运行入口。
    (APP / "scripts/start.ps1").write_text(
        ". (Join-Path $PSScriptRoot 'environment.ps1')\n"
        "if (-not $env:FITNESS_BODY_LEAN) {$env:FITNESS_BODY_LEAN='1'}\n"
        "if (-not $env:FITNESS_MOVEMENT_GUIDANCE) {$env:FITNESS_MOVEMENT_GUIDANCE='1'}\n"
        "& (Join-Path $FitnessProjectRoot '.venv\\Scripts\\python.exe') -B -m fitness_app @args\n"
        "exit $LASTEXITCODE\n", encoding="utf-8",
    )


def verify():
    # 仅运行CPU资源摘要检查及本次新工程的启动/HTTP检查，不触碰原服务。
    result = subprocess.run([str(PYTHON), "-B", "scripts/setup_resources.py", "--check"],
                            cwd=APP, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("交付资源校验失败：" + result.stderr)
    for p in (APP / "fitness_app").rglob("*.py"):
        ast.parse(p.read_text(encoding="utf-8-sig"), filename=str(p.relative_to(APP)))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = os.environ.copy()
    env.update({"TEMP": str(BUILD), "TMP": str(BUILD), "PYTHONUTF8": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "FITNESS_BODY_LEAN": "1",
                "FITNESS_MOVEMENT_GUIDANCE": "1", "FITNESS_PRETRAINED_CORRECTION": "0",
                "FITNESS_DATABASE_PATH": str(APP / (".tmp/smoke-" + __import__("uuid").uuid4().hex + ".sqlite3")),
                "FITNESS_RUNTIME_DIR": ".tmp/smoke-runtime"})
    log = BUILD / "package-smoke.log"
    with log.open("wb") as stream:
        proc = subprocess.Popen([str(PYTHON), "-B", "-m", "fitness_app", "serve", "--port", str(port)],
                                cwd=APP, env=env, stdout=stream, stderr=stream,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            response = None
            for _ in range(60):
                if proc.poll() is not None:
                    raise RuntimeError("交付工程启动退出，参见任务内日志")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/health", timeout=1) as r:
                        response = json.load(r)
                    break
                except OSError:
                    time.sleep(0.5)
            if response is None:
                raise RuntimeError("交付工程启动超时")
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=4) as r:
                assert r.status == 200
                page = r.read().decode("utf-8")
                assert "html" in page.lower()
            health = response.get("data", response)
            assert health["project_id"] == "fitness-assistant"
            assert 'data-kind="login"' in page and 'href="/register"' in page and '--init-admin' not in page
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/register', timeout=4) as r:
                register_page = r.read().decode('utf-8')
                assert 'data-kind="register"' in register_page and 'data-first-registration="true"' in register_page
            from http.cookiejar import CookieJar
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
            base = f'http://127.0.0.1:{port}'
            def post_api(path, data):
                token = json.load(opener.open(base + '/api/v1/auth/csrf'))['data']['csrf_token']
                req = urllib.request.Request(base + path, json.dumps(data).encode(),
                    headers={'Content-Type': 'application/json', 'X-CSRFToken': token})
                with opener.open(req) as res: return json.load(res)
            import secrets
            password = secrets.token_urlsafe(20)
            post_api('/api/v1/auth/setup', {'email': 'isolated_review', 'password': password,
                                           'password_confirmation': password})
            post_api('/api/v1/auth/login', {'email': 'isolated_review', 'password': password})
            with opener.open(base + '/api/v1/auth/me') as res:
                assert json.load(res)['data']['username'] == 'isolated_review'
            for route in ['/records', '/video', '/coach', '/account/training-profile']:
                with opener.open(base + route) as res: assert res.status == 200
            from urllib.parse import quote
            assets = ['/static/round4/' + quote(name) for name in
                      ['认证共用背景.webp', '知形横版标识.svg', '首页动作调整前.webp', '首页动作调整后.webp']]
            for route in assets:
                with opener.open(base + route) as res: assert res.status == 200 and res.read()
            report = {
                "资源核验": result.stdout.strip(), "Python源码语法": "通过",
                "实际启动": "通过", "健康接口": "HTTP 200",
                "登录页面": "登录与注册入口正常", "首次设置与登录": "真实HTTP通过",
                "登录后页面": "记录、视频、AI教练、训练档案HTTP 200",
                "界面资源": "背景、标志、首页示意图HTTP 200", "临时端口": port,
                "范围": "复用本机已有Python依赖，验证解压工程可启动；未重新安装全套依赖、未运行GPU效果或真实AI测试",
            }
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=15)
    (OUT / "补充成果附件/E07_交付工程核验.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)


def package(frozen_docs=None):
    final = OUT / "知练同行_参赛打包文件.zip"
    docs = {
        "知练同行_OPC创新实践说明.pdf": "03_作品介绍文档",
        "知练同行_作品介绍文档.pdf": "03_作品介绍文档",
        "知练同行_运行与评审体验说明.pdf": "04_运行与使用说明",
        "知练同行_补充成果材料.pdf": "05_补充成果材料",
        "知练同行_第三方资源说明.pdf": "06_第三方资源说明",
        "知练同行_人工智能工具使用情况说明.pdf": "07_AI工具使用说明",
    }
    targets = {p: "01_作品及成果文件/作品工程/" + str(p.relative_to(APP)).replace("\\", "/")
               for p in APP.rglob("*") if p.is_file() and eligible(p.relative_to(APP))}
    targets.update({OUT / d: folder + "/" + d for d, folder in docs.items()})
    for p in (OUT / "作品截图").glob("*"):
        if p.is_file():
            targets[p] = "作品截图/" + p.name
    for p in (OUT / "补充成果附件").glob("*"):
        if p.is_file():
            targets[p] = "05_补充成果材料/" + p.name
    video = OUT / "知练同行_作品展示视频.mp4"
    if not video.exists():
        raise RuntimeError("作品展示视频尚未完成")
    targets[video] = "02_作品展示视频/" + video.name
    with ZipFile(final, "w", compression=ZIP_DEFLATED, compresslevel=6) as z:
        for p, arc in targets.items():
            if frozen_docs and p.parent == OUT and p.name in frozen_docs:
                z.writestr("知练同行/" + arc, frozen_docs[p.name])
            else:
                z.write(p, "知练同行/" + arc)
        z.writestr('知练同行/启动评审体验.cmd',
                   '@echo off\nchcp 65001 >nul\ncall "%~dp001_作品及成果文件\\作品工程\\启动评审体验.cmd"\n')
        z.writestr("知练同行/材料阅读顺序.txt",
                   "先阅读作品介绍和运行说明，再进入作品工程。\n"
                   "补充成果材料附机器可读证据。\n"
                   "报名、承诺书、截图、评审视频和PPT按官方对应栏目分别提交。\n")
    with ZipFile(final) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert not any(any(token in n for token in ["/.git/", "/.cache/", "/.tmp/", "auth-secret", ".sqlite3"])
                       for n in names)
    print("PACKAGE", final.stat().st_size, "bytes", len(targets), "files", flush=True)


if __name__ == "__main__":
    prepare()
    verify()
    package()
