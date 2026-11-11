"""获取固定版本的推理资源；只写入本项目，不安装全局依赖。"""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
VERSION = "1.1.1"
MODEL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"


def fetch(url):
    with urlopen(url, timeout=90) as response:
        return response.read()


def main():
    dest = ROOT / "web" / "vendor"
    manifest_path = dest / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get('version') == VERSION and all((dest / name).is_file() and hashlib.sha256((dest / name).read_bytes()).hexdigest() == digest
               for name, digest in manifest["sha256"].items()):
            print("Inference assets already verified.")
            return
    metadata = json.loads(fetch(f"https://registry.npmjs.org/@mediapipe/tasks-vision/{VERSION}"))
    archive = fetch(metadata["dist"]["tarball"])
    expected = metadata["dist"]["integrity"].split("sha512-", 1)[1]
    if base64.b64encode(hashlib.sha512(archive).digest()).decode() != expected:
        raise RuntimeError("Package integrity check failed")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            relative = member.name.removeprefix("package/")
            if relative in {"vision_bundle.js", "LICENSE"} or (
                relative.startswith("wasm/") and "_module" not in relative and relative.endswith((".wasm", ".js"))):
                if not member.isfile() or ".." in Path(relative).parts:
                    raise RuntimeError("Unexpected package member")
                files[relative] = tar.extractfile(member).read()
    if "vision_bundle.js" not in files or not any(n.endswith(".wasm") for n in files):
        raise RuntimeError("Expected runtime files missing")
    files["pose_landmarker_lite.task"] = fetch(MODEL)
    files["LICENSE"] = fetch("https://raw.githubusercontent.com/google-ai-edge/mediapipe/master/LICENSE")
    for name, content in files.items():
        output = (dest / name).resolve()
        if not output.is_relative_to(ROOT):
            raise RuntimeError("Output outside project")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    manifest = {"package": "@mediapipe/tasks-vision", "version": VERSION,
                "package_url": metadata["dist"]["tarball"], "model_url": MODEL,
                "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(files)} assets, {sum(map(len, files.values())) / 1024**2:.1f} MiB, under {dest}")


if __name__ == "__main__":
    main()
