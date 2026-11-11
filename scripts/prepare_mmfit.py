"""下载MM-Fit官方CC BY 4.0 RGB子集，核对Zenodo公开MD5并记录SHA256。"""
import argparse,hashlib,json,sys,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json

DIRECTORY=ROOT/'data/datasets/mmfit'


def run(workouts):
    manifest_path=DIRECTORY/'manifest.json'
    if manifest_path.exists():manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    else:
        with urllib.request.urlopen('https://zenodo.org/api/records/7672767',timeout=45) as response:metadata=json.load(response)
        if metadata['metadata']['license']['id']!='cc-by-4.0':raise ValueError('MMFit许可声明改变')
        manifest={'schema_version':1,'source_url':'https://zenodo.org/records/7672767','doi':'10.5281/zenodo.7672767',
                  'license':'CC BY 4.0','license_url':'https://creativecommons.org/licenses/by/4.0/',
                  'authors':['David Strömbäck','Sangxia Huang','Valentin Radu'],'files':{r['key']:r for r in metadata['files']},
                  'use':'Local action recognition research; cite original paper, preserve source and license, identify extracted clips; no specific-error truth'}
        atomic_json(manifest_path,manifest)
    for workout in workouts:
        filename=workout+'_rgb.mp4'
        if filename not in manifest['files']:raise ValueError('不是记录中的RGB文件')
        item=manifest['files'][filename];target=project_path(DIRECTORY/filename);part=target.with_suffix('.mp4.part')
        if target.exists():
            digest=hashlib.sha256(target.read_bytes()).hexdigest()
            if digest!=item.get('sha256'):raise ValueError('已有MMFit视频摘要改变')
        else:
            offset=part.stat().st_size if part.exists() else 0
            headers={'User-Agent':'FitnessResearch/1.0'}
            if offset:headers['Range']=f'bytes={offset}-'
            with urllib.request.urlopen(urllib.request.Request(item['links']['self'],headers=headers),timeout=90) as response:
                if offset and (response.status!=206 or not response.headers.get('Content-Range','').startswith(f'bytes {offset}-')):raise ValueError('MMFit服务器没有正确续传')
                with part.open('ab' if offset else 'xb') as output:
                    while block:=response.read(1024*1024):
                        offset+=len(block)
                        if offset>item['size']:raise ValueError('视频超过声明大小')
                        output.write(block)
            if part.stat().st_size!=item['size']:raise ValueError('视频下载不完整')
            md5=hashlib.md5();sha=hashlib.sha256()
            with part.open('rb') as stream:
                while block:=stream.read(1024*1024):md5.update(block);sha.update(block)
            if 'md5:'+md5.hexdigest()!=item['checksum']:raise ValueError('MMFit原片MD5不符')
            part.replace(target);item.update(sha256=sha.hexdigest(),path=target.relative_to(ROOT).as_posix());atomic_json(manifest_path,manifest)
        sources_path=ROOT/'data/datasets/development-movements/sources.json';sources=json.loads(sources_path.read_text(encoding='utf-8'))
        key='mmfit-'+workout
        if not any(r['id']==key for r in sources['sources']):
            sources['sources'].append({'id':key,'path':target.relative_to(ROOT).as_posix(),'sha256':item['sha256'],'bytes':item['size'],
                                       'title':'MM-Fit '+workout,'source_url':manifest['source_url'],'author':'; '.join(manifest['authors']),
                                       'license':manifest['license'],'license_url':manifest['license_url'],'source_group':'mmfit_all_workouts',
                                       'ground_truth':'Publisher exercise intervals and coarse rep counts; no independent specific-error labels',
                                       'role':'training_only_entire_MMFit_publisher_group; existing other publishers remain validation',
                                       'action_id':'mixed_workout','awaiting_segment_review':True})
            atomic_json(sources_path,sources)
        print(json.dumps({'workout':workout,'bytes':item['size'],'verified':True}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--videos',default='w19,w16,w17');run(parser.parse_args().videos.split(','))
