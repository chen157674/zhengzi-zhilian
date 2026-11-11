"""核对已有弯举训练输入：解码真实时间、复用姿态裁取上肢，不运行模型或改标签。"""
import hashlib,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av,numpy as np
from PIL import Image,ImageDraw
from fitness_app.media import display_rgb
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/round3'

def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):value.update(block)
    return value.hexdigest()

def decode(entry,desired):
    result=[]
    with av.open(str(project_path(entry['path']))) as container:
        stream=container.streams.video[0]
        container.seek(max(0,int((desired[0]-100)/1000/stream.time_base)),stream=stream,backward=True,any_frame=False)
        for frame in container.decode(stream):
            if frame.time is None:continue
            stamp=round(float(frame.time)*1000)
            if stamp<desired[len(result)]:continue
            if stamp-desired[len(result)]>100:raise ValueError('真实解码时间偏离目标，不能用未来关键帧替代')
            result.append((stamp,display_rgb(frame)))
            if len(result)==len(desired):return result
    raise ValueError('原片不足以提供全部真实采样')

def upper(rgb,points):
    joint=points[[11,12,13,14,15,16]]
    valid=np.isfinite(joint).all(axis=1)&(joint[:,3]>.5)&(joint[:,4]>.5)
    if valid.sum()<4:return rgb,None
    xy=joint[valid,:2];lo=xy.min(axis=0);hi=xy.max(axis=0);size=np.maximum(hi-lo,[.22,.22]);lo=np.clip(lo-size*.4,0,1);hi=np.clip(hi+size*.4,0,1)
    h,w=rgb.shape[:2];box=[round(lo[0]*w),round(lo[1]*h),round(hi[0]*w),round(hi[1]*h)]
    if box[2]-box[0]<32 or box[3]-box[1]<32:return rgb,None
    return rgb[box[1]:box[3],box[0]:box[2]],box

def main():
    source=ROOT/'data/verification/step-22/semantic-video-features.json';all_rows=json.loads(source.read_text(encoding='utf-8'))['entries']
    ids=['mmfit-w19-1461800-1482833','mmfit-w16-2027600-2046533','mmfit-w17-1509800-1528067']
    rows=[next(r for r in all_rows if r['id']==key) for key in ids]
    sheet=Image.new('RGB',(1440,3*310),'white');draw=ImageDraw.Draw(sheet);review=[];verified={}
    for row_index,entry in enumerate(rows):
        path=project_path(entry['path'])
        verified.setdefault(entry['path'],sha(path))
        if verified[entry['path']]!=entry['source_sha256']:raise ValueError('原视频摘要已变化')
        pose=ROOT/'data/verification/step-22'/(entry['id']+'-full-world-features.npz')
        with np.load(pose,allow_pickle=False) as a:times=a['timestamps'].copy();points=a['points'].copy()
        center=(entry['start_ms']+entry['end_ms'])//2;desired=[center-1200,center-400,center+400,center+1200]
        actual=[];boxes=[]
        for column,(stamp,rgb) in enumerate(decode(entry,desired)):
            index=int(np.argmin(abs(times-stamp)))
            if abs(int(times[index])-stamp)>100:raise ValueError('没有对应时刻姿态，不能跨时段裁取')
            patch,box=upper(rgb,points[index]);picture=Image.fromarray(patch);picture.thumbnail((352,254));x=column*360;y=row_index*310
            draw.text((x+6,y+4),entry['id']+' / '+str(stamp)+'ms',fill='black');draw.text((x+6,y+20),'raw crop '+str(patch.shape[1])+'x'+str(patch.shape[0]),fill='black');sheet.paste(picture,(x+4,y+44));actual.append(stamp);boxes.append(box)
        review.append({k:entry[k] for k in ('id','path','partition','group','source_sha256','start_ms','end_ms')}|{'actual_timestamps_ms':actual,'pixel_crops':boxes,'classification_label_not_modified':True})
    target=OUT/'curl-mmfit-grip-review.jpg';sheet.save(target,quality=94)
    atomic_json(OUT/'curl-mmfit-grip-review.json',{'schema_version':1,'purpose':'exact-input grip visibility review; not independent error truth','input_index_sha256':sha(source),'script_sha256':sha(Path(__file__)),'new_model_inferences':0,'rows':review,'contact_sheet':target.relative_to(ROOT).as_posix()})
    print(json.dumps({'reviewed':len(review),'new_model_inferences':0,'contact_sheet':str(target)},ensure_ascii=False))

if __name__=='__main__':main()
