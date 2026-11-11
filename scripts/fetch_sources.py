"""按已核实提交取得适用上游文件；不执行下载代码或覆盖本地修改。"""
from __future__ import annotations

import concurrent.futures
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    ("yakupzengin/fitness-trainer-pose-estimation", "ad01fa37ab18e29586b61c8bacfeb42ce0dc06ad"),
    ("NgoQuocBao1010/Exercise-Correction", "202a0a802d8d2e3ea42f00e6a8c47da9cafc09d7"),
    ("shahmir2004/exercise-form-correction", "b1e56f009f4db193514216914ddb4b69c49c684a"),
    ("rafacarpiom/rdl-form-analyzer", "6d9ddd798a3c88065216c16eb1e7269ab48a242c"),
]


def read_url(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "fitness-assistant-source-fetch/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        content = response.read()
        length = response.headers.get("Content-Length")
        if length and int(length) != len(content):
            raise ValueError(f"下载长度不符：{url}")
        return content


def wanted(repo: str, item: dict) -> bool:
    path = item["path"]
    if item["type"] != "blob" or not item.get("size"):
        return False
    if repo.startswith("yakupzengin/"):
        return path in {"LICENSE", "README.md", "requirements.txt"} or (
            path.endswith((".py", ".yaml", ".html", ".css", ".js"))
            and not path.startswith(("venv/", "env/", "."))
        )
    if repo in {"shahmir2004/exercise-form-correction","rafacarpiom/rdl-form-analyzer"}:
        return path in {'LICENSE','README.md','requirements.txt','pyproject.toml'} or (
            path.startswith(('backend/','src/biomechanics/','src/pipeline/','tests/'))
            and path.endswith(('.py','.yaml','.yml','.json','.md','.toml','.txt')) and item['size']<=300000)
    return path in {"LICENSE", "README.md", "requirements.txt", "core/README.md"} or (
        path.startswith("web/server/detection/") and path.endswith(".py")
    ) or (path.startswith("web/server/static/model/") and path.endswith(".pkl")) or (
        path.startswith("core/") and path.endswith("README.md")
    )


def fetch_one(repo: str, commit: str, item: dict) -> dict:
    rel = PurePosixPath(item["path"])
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("上游文件路径非法")
    target = ROOT / "third_party" / repo.split("/")[1] / Path(*rel.parts)
    target.resolve().relative_to(ROOT)
    if target.exists():
        data = target.read_bytes()
    else:
        data = read_url(f"https://raw.githubusercontent.com/{repo}/{commit}/{rel.as_posix()}")
    git_hash = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    if git_hash != item["sha"]:
        raise ValueError(f"源文件校验失败或已有本地改动，不覆盖：{target.relative_to(ROOT)}")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return {"path": rel.as_posix(), "bytes": len(data), "git_blob": git_hash,
            "sha256": hashlib.sha256(data).hexdigest()}


def main() -> None:
    manifest = ROOT / "third_party" / "sources.json"
    existing=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else []
    records = {item['repo']:item for item in existing}
    for repo, commit in SOURCES:
        tree = json.loads(read_url(f"https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1"))
        if tree.get("truncated") or tree.get("sha") != commit:
            raise ValueError("上游树不完整或提交不匹配")
        items = [item for item in tree["tree"] if wanted(repo, item)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            files = list(pool.map(lambda item: fetch_one(repo, commit, item), items))
        license_path = ROOT / "third_party" / repo.split("/")[1] / "LICENSE"
        if "MIT License" not in license_path.read_text(encoding="utf-8"):
            raise ValueError(f"许可需另行核对：{repo}")
        records[repo]={**records.get(repo,{}),"repo": repo, "commit": commit, "license": "MIT", "files": files}
        print(f"{repo}: {len(files)} files, {sum(f['bytes'] for f in files)} bytes", flush=True)
    manifest.write_text(json.dumps(list(records.values()), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    model_path = ROOT / "data" / "models" / "pose_landmarker_full.task"
    url = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/1/pose_landmarker_full.task"
    if not model_path.exists():
        data = read_url(url)
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(data)
    if not zipfile.is_zipfile(model_path):
        raise ValueError("官方 Tasks 模型容器无效")
    model_record = {"source": url, "variant": "full", "revision": "float16/1",
                    "bytes": model_path.stat().st_size,
                    "sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                    "license": "Apache-2.0",
                    "license_evidence": "https://github.com/google-ai-edge/mediapipe/issues/6306",
                    "note": "官方维护者明确该版本模型适用 Apache 2；不适用健身项目 MIT 许可"}
    (model_path.parent / "manifest.json").write_text(
        json.dumps(model_record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"MediaPipe Full: {model_record['bytes']} bytes", flush=True)


def prepare_visual_backbone():
    """只准备新增视觉骨干；不重取既有四项目或覆盖姿态模型清单。"""
    repo='opencv/opencv_zoo';commit='48b10fa505aeb51fbd127fc60a5c660040e823b7';folder='models/image_classification_mobilenet'
    tree=json.loads(read_url(f'https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1'))
    if tree.get('truncated') or tree.get('sha')!=commit:raise ValueError('视觉骨干提交树不完整')
    wanted_files={'LICENSE',folder+'/LICENSE',folder+'/README.md',folder+'/mobilenet.py'}
    selected=[item for item in tree['tree'] if item['path'] in wanted_files]
    if len(selected)!=4:raise ValueError('视觉骨干许可／说明／预处理源码不完整')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        files=list(pool.map(lambda item:fetch_one(repo,commit,item),selected))
    license_path=ROOT/'third_party/opencv_zoo'/folder/'LICENSE'
    if 'Apache License' not in license_path.read_text(encoding='utf-8'):raise ValueError('模型目录许可不符')
    pointer_item=next(item for item in tree['tree'] if item['path']==folder+'/image_classification_mobilenetv2_2022apr.onnx')
    blob=json.loads(read_url(pointer_item['url']));pointer=base64.b64decode(blob['content'])
    if hashlib.sha1(f'blob {len(pointer)}\0'.encode()+pointer).hexdigest()!=pointer_item['sha']:raise ValueError('LFS指针身份不符')
    values=dict(line.split(' ',1) for line in pointer.decode().splitlines())
    expected=values['oid'].removeprefix('sha256:');size=int(values['size'])
    if len(expected)!=64 or not 1_000_000<size<25_000_000:raise ValueError('模型大小或摘要异常')
    target=ROOT/'data/models/mobilenetv2.onnx';target.resolve().relative_to(ROOT)
    url=f'https://media.githubusercontent.com/media/{repo}/{commit}/{pointer_item["path"]}'
    print(json.dumps({'phase':'visual_backbone_prepare','expected_bytes':size,'existing':target.exists()}),flush=True)
    payload=target.read_bytes() if target.exists() else read_url(url)
    if len(payload)!=size or hashlib.sha256(payload).hexdigest()!=expected:raise ValueError('视觉骨干不符合官方LFS摘要，不覆盖')
    if not target.exists():
        with target.open('xb') as output:output.write(payload)
    record={'source':url,'repo':repo,'commit':commit,'bytes':size,'sha256':expected,'license':'Apache-2.0',
            'license_file':license_path.relative_to(ROOT).as_posix(),'license_evidence':f'https://github.com/{repo}/blob/{commit}/{folder}/README.md#license',
            'lfs_pointer_git_blob':pointer_item['sha'],'role':'仅内部整段GB/PB视觉特征候选；ImageNet输出不是姿态错误标签'}
    target.with_suffix('.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest=ROOT/'third_party/sources.json';records=json.loads(manifest.read_text(encoding='utf-8'))
    records=[item for item in records if item['repo']!=repo]+[{'repo':repo,'commit':commit,'license':'Apache-2.0','files':files,'purpose':record['role']}]
    manifest.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'phase':'visual_backbone_ready','model':str(target),'bytes':size,'sha256':expected,'source_files':len(files)}),flush=True)


if __name__ == "__main__":
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--visual-backbone',action='store_true');args=parser.parse_args()
    prepare_visual_backbone() if args.visual_backbone else main()
