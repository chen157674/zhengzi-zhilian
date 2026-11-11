"""核查新增公开视频的实际帧/编解码；冻结素材只读容器，不解码画面或调用模型。"""
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

IDS=('qgnRMcTipRw','38wP7lWuQ2o','5-p83j9FS0A','rx6arUUtbtE','h5A0JKbVDDA','IeOqdw9WI90')


def main():
    directory=ROOT/'data/datasets/development-movements';sources=json.loads((directory/'sources.json').read_text(encoding='utf-8'))['sources'];sources={r['id']:r for r in sources}
    records=[];pictures=[];unavailable=[];reserved=[]
    for vid in IDS:
        key='youtube-'+vid
        if key not in sources:unavailable.append(key);continue
        source=sources[key];path=project_path(source['path'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']:raise ValueError('新增原片摘要改变')
        with av.open(str(path)) as video:
            if len(video.streams.video)!=1 or video.streams.audio:raise ValueError('新增视频必须只有一条视频流、无音频')
            stream=video.streams.video[0];duration=round(float(video.duration)/1000) if video.duration is not None else round(source['duration_seconds']*1000)
            media={'codec':stream.codec_context.name,'width':stream.width,'height':stream.height,'duration_ms':duration,'video_streams':1,'audio_streams':0}
        row={'id':key,'path':source['path'],'source_sha256':source['sha256'],'group':source['source_group'],'partition':source['partition'],'media':media,'title':source['title'],'application_model_inferences':0,'independent_error_truth':False}
        if source.get('model_inference_allowed_before_freeze') is False or source['partition']=='reserved_holdout':
            reserved.append(key);records.append(row|{'decoded_frame_count':0,'sampled_frames':0,'actual_ms':[],'scope':'container/license only; no raw frame or application inference'});continue
        enforce_source_policy([row],operation='inference');count=16 if vid=='38wP7lWuQ2o' else 8;desired=np.linspace(1000,max(1500,duration-1000),count);images=[];times=[];decoded=0
        with av.open(str(path)) as video:
            for frame in video.decode(video=0):
                decoded+=1
                if frame.time is None:continue
                stamp=round(float(frame.time)*1000)
                if stamp<desired[len(images)]:continue
                images.append(display_rgb(frame));times.append(stamp)
                if len(images)==count:break
        if len(images)!=count:raise ValueError('没有足够确切原帧进行素材核查')
        records.append(row|{'decoded_frame_count':decoded,'sampled_frames':len(images),'actual_ms':times,'scope':'developer source inspection only; no independent error labels'})
        pictures.append((key,images,times))
    height=sum((len(images)+3)//4*200 for _,images,_ in pictures)
    if not height:raise ValueError('没有可核查的非预留素材')
    sheet=Image.new('RGB',(1280,height),'white');draw=ImageDraw.Draw(sheet);base=0
    for key,images,times in pictures:
        for j,(rgb,stamp) in enumerate(zip(images,times)):
            image=Image.fromarray(rgb);image.thumbnail((312,166));x=j%4*320;y=base+j//4*200;sheet.paste(image,(x+4,y+27));draw.text((x+4,y+5),key.removeprefix('youtube-')+' / '+str(stamp)+'ms',fill='black')
        base+=(len(images)+3)//4*200
    target=project_path(ROOT/'data/verification/round3/round3-supplement-source-review.jpg');sheet.save(target,quality=94)
    atomic_json(target.with_suffix('.json'),{'schema_version':1,'rows':records,'unavailable':unavailable,'frozen_without_frame_decode':reserved,'application_model_inferences':0,
        'contact_sheet':target.relative_to(ROOT).as_posix(),'contact_sheet_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print(json.dumps({'registered':len(records),'raw_review_sources':len(pictures),'raw_review_frames':sum(len(v[1]) for v in pictures),'frozen_container_only':reserved,'unavailable':unavailable,'contact_sheet':str(target)}),flush=True)


if __name__=='__main__':main()
