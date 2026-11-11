"""固定版本下载许可明确的视频模型；仅取safetensors与配置，不执行远程代码。"""
import argparse,hashlib,json,sys,urllib.request,urllib.parse
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json

MODELS={'xclip':('microsoft/xclip-base-patch32','xclip-base-patch32','MIT'),
        'smolvlm':('HuggingFaceTB/SmolVLM2-500M-Video-Instruct','smolvlm2-500m-video','Apache-2.0'),
        'qwen-vl':('Qwen/Qwen3-VL-2B-Instruct','qwen3-vl-2b-instruct','Apache-2.0'),
        'qwen-vl-4b':('Qwen/Qwen3-VL-4B-Instruct','qwen3-vl-4b-instruct','Apache-2.0')}


def run(name):
    repo,folder,license=MODELS[name];directory=project_path(ROOT/'data/models'/folder)
    manifest_path=directory/'manifest.json'
    if manifest_path.exists():info=json.loads(manifest_path.read_text(encoding='utf-8'))
    else:
        with urllib.request.urlopen('https://huggingface.co/api/models/'+repo+'?blobs=true',timeout=45) as response:metadata=json.load(response)
        allowed=lambda n:n.endswith('.safetensors') or n=='README.md' or n.endswith('.json') or n in ('merges.txt','vocab.json','LICENSE')
        files={row['rfilename']:{'bytes':row.get('size'),'expected_sha256':row.get('lfs',{}).get('sha256'),'git_blob':row.get('blobId')} for row in metadata['siblings'] if '/' not in row['rfilename'] and allowed(row['rfilename'])}
        info={'schema_version':1,'repo':repo,'revision':metadata['sha'],'license':license,
              'license_source':'https://huggingface.co/'+repo,'status':'preparing','files':files,
              'usage':'Local development comparison only; not medical or independent ground truth; trust_remote_code=False; safetensors only'}
        atomic_json(manifest_path,info)
    if info['repo']!=repo:raise ValueError('已有模型归属不符')
    for filename,item in info['files'].items():
        path=project_path(directory/filename);part=path.with_suffix(path.suffix+'.part')
        if path.exists():
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if item.get('sha256')==digest:continue
            raise ValueError('已有模型文件未通过摘要校验')
        offset=part.stat().st_size if part.exists() else 0
        if offset and (not item.get('bytes') or offset>=item['bytes']):raise ValueError('下载中间文件大小异常')
        url='https://huggingface.co/'+repo+'/resolve/'+info['revision']+'/'+urllib.parse.quote(filename)
        headers={'User-Agent':'FitnessResearch/1.0'}
        if offset:headers['Range']=f'bytes={offset}-'
        request=urllib.request.Request(url,headers=headers)
        with urllib.request.urlopen(request,timeout=90) as response:
            if offset and (response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {offset}-')):raise ValueError('源站未正确续传，保留已下载部分')
            with part.open('ab' if offset else 'xb') as output:
                while block:=response.read(1024*1024):
                    output.write(block);offset+=len(block)
                    if item.get('bytes') and offset>item['bytes']:raise ValueError('超过模型声明大小')
        digest=hashlib.sha256(part.read_bytes()).hexdigest()
        if item.get('bytes')!=part.stat().st_size or item.get('expected_sha256') and item['expected_sha256']!=digest:raise ValueError('模型下载摘要或大小不同')
        if not item.get('expected_sha256') and item.get('git_blob'):
            content=part.read_bytes();blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
            if blob!=item['git_blob']:raise ValueError('模型配置Git对象不同')
        part.replace(path);item['sha256']=digest;atomic_json(manifest_path,info)
        print(json.dumps({'model':name,'file':filename,'bytes':item['bytes']}),flush=True)
    info['status']='verified';atomic_json(manifest_path,info)
    print(json.dumps({'model':name,'revision':info['revision'],'verified':True}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--model',choices=MODELS,required=True);run(parser.parse_args().model)
