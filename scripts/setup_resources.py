"""恢复固定版本模型并核对摘要；默认准备CPU姿态评审资源。"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def target(relative):
    path = (ROOT / relative).resolve()
    path.relative_to(ROOT)
    return path


def valid(asset):
    path = target(asset["path"])
    return (
        path.is_file()
        and path.stat().st_size == asset["bytes"]
        and hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]
    )


def download(url):
    last_error = None
    for opener in (
        urllib.request.build_opener(),
        urllib.request.build_opener(urllib.request.ProxyHandler({})),
    ):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "GuanDongZhiZi-resource-setup"})
            with opener.open(request, timeout=180) as response:
                return response.read()
        except (OSError, urllib.error.URLError) as error:
            last_error = error
    raise RuntimeError("资源下载失败：" + url) from last_error


def save(asset, content):
    if len(content) != asset["bytes"] or hashlib.sha256(content).hexdigest() != asset["sha256"]:
        raise ValueError("资源摘要或大小不匹配：" + asset["path"])
    path = target(asset["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        with part.open("xb") as stream:
            stream.write(content)
        part.replace(path)
    finally:
        if part.exists():
            part.unlink()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只核对已有文件，不联网")
    parser.add_argument("--profile", choices=("core", "full"), default="core")
    args = parser.parse_args()
    registry = json.loads(target("资源清单.json").read_text(encoding="utf-8"))
    pose = registry["pose"]
    pose_asset = {
        "path": "data/models/pose_landmarker_full.task",
        "bytes": pose["bytes"], "sha256": pose["sha256"],
    }
    assets = [pose_asset] + [
        asset for model in registry["models"].values() for asset in model["files"]
    ]
    if args.profile == "full":
        assets += registry["full_profile"]
    missing = [asset for asset in assets if not valid(asset)]
    if args.check:
        if missing:
            for asset in missing:
                print("待准备：" + asset["path"], file=sys.stderr)
            return 1
        print(f"已核对{len(assets)}个固定版本资源，摘要一致。")
        return 0
    if not valid(pose_asset):
        save(pose_asset, download(pose["source"]))
    for name, model in registry["models"].items():
        if all(valid(asset) for asset in model["files"]):
            continue
        print("准备：" + name, flush=True)
        archive = download(model["archive_url"])
        if hashlib.sha256(archive).hexdigest() != model["archive_sha256"]:
            raise ValueError("模型压缩包摘要不匹配：" + name)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            members = [entry for entry in bundle.infolist() if not entry.is_dir()]
            for asset in model["files"]:
                if valid(asset):
                    continue
                filename = Path(asset["path"]).name
                matches = [entry for entry in members if Path(entry.filename).name == filename]
                contents = [
                    bundle.read(entry) for entry in matches
                    if entry.file_size == asset["bytes"]
                ]
                content = next(
                    (value for value in contents if hashlib.sha256(value).hexdigest() == asset["sha256"]),
                    None,
                )
                if content is None:
                    raise ValueError("压缩包中未找到匹配资源：" + asset["path"])
                save(asset, content)
    if args.profile == "full":
        for asset in registry["full_profile"]:
            if not valid(asset):
                print("准备：" + asset["path"], flush=True)
                save(asset, download(asset["url"]))
    print(f"资源准备完成，共核对{len(assets)}个文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
