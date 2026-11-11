"""查看拟消费的确切8帧，训练/验证补充原帧核查不读取保留或权属待核验来源。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from PIL import Image,ImageDraw
from scripts.probe_new_actions import images
from fitness_app.research_extract import atomic_json


def main():
    directory=ROOT/'data/datasets/development-movements';sources={r['id']:r for r in json.loads((directory/'sources.json').read_text(encoding='utf-8'))['sources']}
    specs=[('youtube-H4t8DqWu2Ng',8000,28000),('youtube-x3L3SEPwbrg',1000,12000)];sheet=Image.new('RGB',(1280,len(specs)*416),'white');draw=ImageDraw.Draw(sheet);rows=[]
    for i,(key,start,end) in enumerate(specs):
        source=sources[key]
        if source.get('model_inference_allowed_before_freeze') is False or source['partition'] not in {'train','validation'}:raise ValueError('不读取预留/权属限制原片')
        if hashlib.sha256(project_path(source['path']).read_bytes()).hexdigest()!=source['sha256']:raise ValueError('原片摘要改变')
        entry={'id':key+f'-{start}-{end}','source_id':key,'path':source['path'],'source_sha256':source['sha256'],'action_id':source['action_id'],'start_ms':start,'end_ms':end,
            'crop':None,'partition':source['partition'],'group':source['source_group']};frames,times=images(entry)
        for j,(rgb,stamp) in enumerate(zip(frames,times)):
            picture=Image.fromarray(rgb);picture.thumbnail((312,176));x=j%4*320;y=i*416+j//4*208;sheet.paste(picture,(x+4,y+25));draw.text((x+4,y+4),source['id'].removeprefix('youtube-')+' / '+str(stamp)+'ms',fill='black')
        rows.append({**entry,'actual_ms':times,'application_model_inferences':0,'independent_error_truth':False})
    target=ROOT/'data/verification/round3/new-cc-active-inputs.jpg';sheet.save(target,quality=94)
    atomic_json(target.with_suffix('.json'),{'schema_version':1,'scope':'developer visual annotation of exact 8-frame source windows, not model scoring or independent coach truth',
        'rows':rows,'contact_sheet':target.relative_to(ROOT).as_posix(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print(json.dumps({'windows':len(rows),'application_model_inferences':0,'contact_sheet':str(target)}))


if __name__=='__main__':main()
