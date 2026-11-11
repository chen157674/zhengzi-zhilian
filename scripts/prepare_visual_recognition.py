"""固定来源下载本地视觉候选；不使用Hub默认缓存，不调用在线推理。"""
import hashlib,json,sys,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()

REPO='Xenova/clip-vit-base-patch32'
REVISION='d15189d7028b43f1d3e65039190477f6af591c2a'
DEST=ROOT/'data/models/clip-vit-base-patch32'
FILES=('config.json','preprocessor_config.json','tokenizer.json','tokenizer_config.json',
       'onnx/text_model_quantized.onnx','onnx/vision_model_quantized.onnx')


def get_json(url):
    with urllib.request.urlopen(url,timeout=40) as r:return json.load(r)


def run():
    DEST.resolve().relative_to(ROOT);DEST.mkdir(exist_ok=True)
    info=get_json(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true')
    if info['sha']!=REVISION:raise ValueError('模型提交不一致')
    upstream=get_json('https://api.github.com/repos/openai/CLIP/commits/main')['sha']
    manifest={'repo':REPO,'revision':REVISION,'base_model':'openai/clip-vit-base-patch32',
              'license':'MIT (upstream OpenAI CLIP; ONNX conversion by Xenova)',
              'upstream_commit':upstream,'source_url':f'https://huggingface.co/{REPO}/tree/{REVISION}',
              'runtime_scope':'offline development candidate; no fitness validation implied','files':{}}
    items={v['rfilename']:v for v in info['siblings']}
    for name in (*FILES,'LICENSE'):
        path=project_path(DEST/name);path.parent.mkdir(exist_ok=True)
        url=f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}' if name!='LICENSE' else f'https://raw.githubusercontent.com/openai/CLIP/{upstream}/LICENSE'
        if not path.exists():
            partial=path.with_suffix(path.suffix+'.part')
            if partial.exists():raise ValueError('已有下载中间文件，请核对来源后恢复')
            try:
                with urllib.request.urlopen(url,timeout=60) as response,partial.open('xb') as output:
                    while chunk:=response.read(1024*1024):output.write(chunk)
                partial.rename(path)
            finally:
                if partial.exists():partial.unlink()
        content=path.read_bytes();digest=hashlib.sha256(content).hexdigest()
        if name!='LICENSE':
            item=items[name]
            if len(content)!=item['size']:raise ValueError('模型文件长度不一致')
            if item.get('lfs'):
                if digest!=item['lfs']['sha256']:raise ValueError('模型权重摘要不一致')
            elif hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()!=item['blobId']:
                raise ValueError('模型配置摘要不一致')
        manifest['files'][name]={'sha256':digest,'bytes':len(content),'url':url}
        print(json.dumps({'file':name,'bytes':len(content)}),flush=True)
    target=DEST/'manifest.json'
    if target.exists():
        old=json.loads(target.read_text(encoding='utf-8'))
        if old['revision']!=REVISION or old['files']!=manifest['files']:raise ValueError('已登记模型来源不同')
    else:target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':run()
