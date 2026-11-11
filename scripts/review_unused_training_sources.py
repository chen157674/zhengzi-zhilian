"""核查原目录尚未使用的训练候选原帧；不调用项目模型，不把标题当动作真值。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.source_partitions import enforce_source_policy
from fitness_app.media import display_rgb
from fitness_app.research_extract import atomic_json
from PIL import Image,ImageDraw
import av,numpy as np

IDS=('youtube-v2ESoWx17tk','youtube-H_QkInZ6t9Q','youtube-_Akq1z8i3fM','youtube-1rUrd6xxGJU','youtube-Hy5L3-QJETc','youtube-fl3zro7uzu4')


def main():
    sources=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources'];sources={r['id']:r for r in sources}
    rows=[];sheet=Image.new('RGB',(1280,len(IDS)*390),'white');draw=ImageDraw.Draw(sheet)
    for i,key in enumerate(IDS):
        source=sources[key];row={'id':key,'source_id':key,'source_sha256':source['sha256'],'group':source['source_group'],'partition':'train','path':source['path']}
        enforce_source_policy([row]);enforce_source_policy([row],operation='inference')
        path=project_path(row['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['source_sha256']:raise ValueError('原片摘要改变')
        desired=np.linspace(1000,max(2000,round(source['duration_seconds']*1000)-1000),8);images=[];times=[]
        with av.open(str(path)) as video:
            for frame in video.decode(video=0):
                if frame.time is None:continue
                stamp=round(float(frame.time)*1000)
                if stamp<desired[len(images)]:continue
                images.append(display_rgb(frame));times.append(stamp)
                if len(images)==8:break
        if len(images)!=8:raise ValueError('候选视频没有8个实际可读帧')
        for j,(rgb,stamp) in enumerate(zip(images,times)):
            picture=Image.fromarray(rgb);picture.thumbnail((312,160));x=j%4*320;y=i*390+j//4*190;sheet.paste(picture,(x+4,y+27));draw.text((x+4,y+5),key.removeprefix('youtube-')+' / '+str(stamp)+'ms',fill='black')
        rows.append(row|{'title':source['title'],'actual_ms':times,'primary_license_declaration':source['copyright_status'],'independent_error_truth':False})
    target=ROOT/'data/verification/round3/unused-training-source-review.jpg';sheet.save(target,quality=94)
    atomic_json(target.with_suffix('.json'),{'schema_version':1,'rows':rows,'contact_sheet':target.relative_to(ROOT).as_posix(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'application_model_inferences':0,'scope':'developer review of original candidates; existing train publishers or unused publisher only, no reserved/validation sources'})
    print(json.dumps({'sources':len(rows),'application_model_inferences':0,'contact_sheet':str(target)}))


if __name__=='__main__':main()
