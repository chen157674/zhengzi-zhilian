"""查看新下载训练/验证候选的实际画面；不打开预留作者保留集，不调用识别模型。"""
import json,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from PIL import Image,ImageDraw
from scripts.review_curl_training_inputs import decode
from fitness_app.research_extract import atomic_json


def main():
    directory=ROOT/'data/datasets/development-movements';selection=json.loads((directory/'cc-round3-source-selection.json').read_text(encoding='utf-8'))
    sources={r['id']:r for r in json.loads((directory/'sources.json').read_text(encoding='utf-8'))['sources']}
    chosen=[r for r in selection['videos'] if r['prospective_role']!='reserved_new_publisher_holdout']
    sheet=Image.new('RGB',(1440,len(chosen)*2*190),'white');draw=ImageDraw.Draw(sheet);reviews=[]
    for row_index,selected in enumerate(chosen):
        source=sources['youtube-'+selected['id']]
        if source.get('model_inference_allowed_before_freeze') is False:raise ValueError('预留来源不进入普通原帧浏览工具')
        media=project_path(source['path']);digest=hashlib.sha256(media.read_bytes()).hexdigest()
        if digest!=source['sha256']:raise ValueError('新原片摘要改变')
        duration=source['download_verified_media']['duration_ms'];desired=np.linspace(500,duration-700,10).round().astype(int).tolist();frames=decode(source,desired)
        times=[]
        for j,(stamp,rgb) in enumerate(frames):
            x=j%5*288;y=row_index*380+j//5*190;image=Image.fromarray(rgb);image.thumbnail((280,158));sheet.paste(image,(x+4,y+24))
            draw.text((x+4,y+3),selected['id']+' / '+str(stamp)+'ms',fill='black');times.append(stamp)
        reviews.append({'id':source['id'],'source_sha256':digest,'path':source['path'],'group':source['source_group'],'prospective_role':selected['prospective_role'],'actual_ms':times,
            'author':source['author'],'source_url':source['source_url'],'license':source['copyright_status'],'no_model_inference':True})
    target=ROOT/'data/verification/round3/new-cc-source-overview.jpg';sheet.save(target,quality=94)
    atomic_json(target.with_suffix('.json'),{'schema_version':1,'scope':'raw-frame developer review only; reserved new publisher excluded; no model correctness or error truth',
        'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'rows':reviews,'contact_sheet':target.relative_to(ROOT).as_posix(),
        'reserved_excluded':['youtube-'+r['id'] for r in selection['videos'] if r['prospective_role']=='reserved_new_publisher_holdout']})
    print(json.dumps({'reviewed_sources':len(reviews),'frames':len(reviews)*10,'reserved_opened':False,'contact_sheet':str(target)},ensure_ascii=False))


if __name__=='__main__':main()
