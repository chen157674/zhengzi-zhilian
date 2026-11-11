"""回看失败预测实际消费的原视频画面；不重提模型特征或按预测改标签。"""
import hashlib,json,sys
from functools import lru_cache
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,project_path,configure_environment
configure_environment()
import av,numpy as np
from PIL import Image,ImageDraw
from fitness_app.research_extract import atomic_json

OUT=ROOT/'data/verification/round3'


@lru_cache(maxsize=32)
def digest(path):return hashlib.sha256(project_path(path).read_bytes()).hexdigest()


def images(entry,times):
    path=project_path(entry['path'])
    if digest(entry['path'])!=entry['source_sha256']:raise ValueError('原视频摘要改变')
    def decode(seek):
        output=[];actual=[];index=0
        with av.open(str(path)) as video:
            video.seek(int(times[0])*1000 if seek else 0)
            for f in video.decode(video=0):
                if f.time is None:continue
                stamp=round(float(f.time)*1000)
                if stamp<times[index]-1:continue
                if stamp-times[index]>50:return None
                rgb=f.to_ndarray(format='rgb24')
                if entry.get('crop'):
                    h,w=rgb.shape[:2];l,t,r,b=entry['crop'];rgb=rgb[round(t*h):round(b*h),round(l*w):round(r*w)]
                output.append(Image.fromarray(rgb));actual.append(stamp);index+=1
                if index==len(times):return output,actual
        return None
    value=decode(True) or decode(False)
    if value is None:raise ValueError('不能还原模型实际采样时间，不替换成未来关键帧')
    return value


def run():
    base=json.loads((OUT/'baseline.json').read_text(encoding='utf-8'))
    source=ROOT/'data/verification/step-22/semantic-video-features.json'
    entries={r['id']:r for r in json.loads(source.read_text(encoding='utf-8'))['entries']}
    failures=[f for a in base['actions'] for f in a['failures'] if not a['meets_round3_development_recall']]
    identity={'baseline':base['signature'],'features':hashlib.sha256(source.read_bytes()).hexdigest(),
              'script':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    target=OUT/'failure-input-review.json'
    if target.exists():
        old=json.loads(target.read_text(encoding='utf-8'))
        if old['identity']==identity and all(project_path(p).is_file() for p in old['contact_sheets']):
            print(json.dumps({'reused':True,'failed_inputs':len(old['rows'])}));return
    rows=[];pages=[]
    for start in range(0,len(failures),4):
        selected=failures[start:start+4];sheet=Image.new('RGB',(1600,len(selected)*280),'white');draw=ImageDraw.Draw(sheet)
        for row,failure in enumerate(selected):
            entry=entries[failure['id']];cache=project_path(entry['cache'])
            with np.load(cache,allow_pickle=False) as c:stamps=c['timestamps'][[0,2,5,7]].astype(int)
            views,actual=images(entry,stamps)
            top=row*280
            draw.text((8,top+2),failure['id'],fill='black')
            draw.text((8,top+18),'label: '+failure['expected']+'  output: '+str(failure['predicted'] or 'unknown'),fill='black')
            for col,(image,stamp) in enumerate(zip(views,actual)):
                image.thumbnail((400,226));x=col*400
                draw.text((x+4,top+34),str(stamp)+' ms',fill='black');sheet.paste(image,(x+(400-image.width)//2,top+52))
            rows.append({'id':entry['id'],'expected':failure['expected'],'predicted':failure['predicted'],
                         'source_sha256':entry['source_sha256'],'cache_sha256':digest(entry['cache']),
                         'timestamps_ms':actual,'contact_page':start//4+1,'contact_row':row,'review_status':'awaiting_visual_review'})
        path=OUT/f'failure-review-{start//4+1}.jpg';sheet.save(path,quality=92);pages.append(path.relative_to(ROOT).as_posix())
    atomic_json(target,{'schema_version':1,'identity':identity,'scope':'inspect_model_inputs_not_independent_error_truth',
                        'new_model_inferences':0,'contact_sheets':pages,'rows':rows})
    print(json.dumps({'failed_inputs':len(rows),'contact_sheets':pages,'new_model_inferences':0}))


if __name__=='__main__':run()
