"""复核本地已有卧推素材的连续动作窗口，不下载、推理或改变原角色。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from PIL import Image,ImageDraw
from scripts.review_curl_training_inputs import decode
from fitness_app.research_extract import atomic_json


def main(detail=False):
    manifest=ROOT/'data/datasets/development-movements/sources.json';source=next(r for r in json.loads(manifest.read_text(encoding='utf-8'))['sources'] if r['id']=='commons-gantner-dumbbell-bench')
    path=project_path(source['path'])
    if hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('原视频摘要改变，拒绝沿用旧复核')
    entry={'path':source['path']};windows=[]
    if detail:
        import numpy as np
        specs=[('g',75500,78200)] if detail=='incline' else [('a',38500,41700),('b',54800,58000),('c',62200,65400),('d',74000,77200),('e',12000,15200),('f',46000,49200)]
        frames=[]
        for key,start,end in specs:
            actual=decode(entry,[int(t) for t in np.linspace(start,end-100,8)])
            windows.append({'id':key,'start_ms':start,'end_ms':end,'actual_ms':[r[0] for r in actual]})
            frames.extend(actual)
        sheet=Image.new('RGB',(1280,len(specs)*2*208),'white');columns=4;width=320;height=208
    else:
        frames=decode(entry,list(range(0,93000,4000)));sheet=Image.new('RGB',(1440,5*190),'white');columns=5;width=288;height=190
    draw=ImageDraw.Draw(sheet);rows=[]
    for i,(stamp,rgb) in enumerate(frames):
        x=(i%columns)*width;y=(i//columns)*height;picture=Image.fromarray(rgb);picture.thumbnail((width-8,height-32))
        label=('window '+windows[i//8]['id']+' / ') if detail else ''
        draw.text((x+4,y+3),label+str(i+1)+' / '+str(stamp)+'ms',fill='black');sheet.paste(picture,(x+4,y+24))
        rows.append({'panel':i+1,'actual_ms':stamp,'size':[rgb.shape[1],rgb.shape[0]]})
    filename='gantner-incline-window.jpg' if detail=='incline' else 'gantner-window-details.jpg' if detail else 'gantner-window-overview.jpg'
    output=ROOT/'data/verification/round3'/filename;sheet.save(output,quality=94)
    atomic_json(output.with_suffix('.json'),{'schema_version':1,'source_id':source['id'],'source_sha256':source['sha256'],'source_path':source['path'],
        'source_url':source['source_url'],'credit':source['author'],'license':source['copyright_status'],'license_url':source['terms_url'],
        'purpose':'continuous-window development review only; not independent full-body or technique truth','new_downloads':0,'new_model_inferences':0,
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'frames':rows,'windows':windows,'contact_sheet':output.relative_to(ROOT).as_posix()})
    print(json.dumps({'frames':len(frames),'contact_sheet':str(output),'new_downloads':0,'new_model_inferences':0}),flush=True)


if __name__=='__main__':
    if sys.argv[1:]==['--detail']:main(True)
    elif sys.argv[1:]==['--incline']:main('incline')
    elif sys.argv[1:]:raise SystemExit('只支持无参数总览、--detail原窗口或--incline后续上斜窗口查看')
    else:main()
