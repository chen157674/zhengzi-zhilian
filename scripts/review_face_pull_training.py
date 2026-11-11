"""核对现有两段面拉训练的确切输入，不重新推理姿态或动作、不读取评估来源。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from PIL import Image,ImageDraw
from scripts.train_chest_expert import cases,OUT,OLD
from scripts.temporal_expert_features import recovering_images
from fitness_app.research_extract import atomic_json


def main():
    rows,expected,observed,_=cases();selected=[r for r in rows if r['partition']=='train' and r['action_id']=='cable_face_pull']
    sheet=Image.new('RGB',(1280,len(selected)*416),'white');draw=ImageDraw.Draw(sheet);result=[]
    for i,row in enumerate(selected):
        media=project_path(row['path']);semantic=project_path(row['cache'])
        if hashlib.sha256(media.read_bytes()).hexdigest()!=row['source_sha256'] or hashlib.sha256(semantic.read_bytes()).hexdigest()!=expected[row['id']]['semantic_cache_sha256']:raise ValueError('训练原片/缓存身份改变')
        with np.load(semantic,allow_pickle=False) as a:times=a['timestamps'].astype(np.int64).tolist()
        images,recovered=recovering_images(row,times)
        for j,(rgb,stamp) in enumerate(zip(images,times)):
            picture=Image.fromarray(rgb);picture.thumbnail((312,176));x=j%4*320;y=i*416+j//4*208;sheet.paste(picture,(x+4,y+24));draw.text((x+4,y+4),row.get('source_id',row['id']).removeprefix('youtube-')+' / '+str(stamp)+'ms',fill='black')
        result.append({k:row[k] for k in ('id','path','source_sha256','start_ms','end_ms','crop','group','partition')}|{'actual_ms':times,'sequential_recovery':recovered,'semantic_cache_sha256':expected[row['id']]['semantic_cache_sha256']})
    target=OUT/'face-pull-training-review.jpg';sheet.save(target,quality=94)
    atomic_json(target.with_suffix('.json'),{'schema_version':1,'scope':'exact training frames only; developer equipment/phase audit, not independent error truth',
        'rows':result,'contact_sheet':target.relative_to(ROOT).as_posix(),'new_application_model_inferences':0,'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print(json.dumps({'reviewed_training':len(result),'contact_sheet':str(target),'new_model_inferences':0}))


if __name__=='__main__':main()
