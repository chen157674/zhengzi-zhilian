"""完整导出24个泛弯举验证输入的实际画面，按原顺序盲化模型预测。"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import numpy as np
from PIL import Image,ImageDraw
from scripts.review_curl_training_inputs import sha,decode,upper
from fitness_app.research_extract import atomic_json


def main():
    old=ROOT/'data/verification/step-22';out=ROOT/'data/verification/round3';index=old/'semantic-video-features.json'
    entries=[r for r in json.loads(index.read_text(encoding='utf-8'))['entries'] if r['partition']=='validation' and r['action_id']=='standing_dumbbell_curl']
    if len(entries)!=24 or any(r['crop'] is not None for r in entries):raise ValueError('原验证输入范围改变，需先重新核对')
    reviews=[];sheets=[]
    for page in range(4):
        sheet=Image.new('RGB',(1440,6*290),'white');draw=ImageDraw.Draw(sheet)
        for line,entry in enumerate(entries[page*6:(page+1)*6]):
            if sha(project_path(entry['path']))!=entry['source_sha256']:raise ValueError('原片SHA改变')
            semantic=project_path(entry['cache'])
            with np.load(semantic,allow_pickle=False) as a:stamps=a['timestamps'].copy()
            desired=[int(stamps[i]) for i in (1,3,4,6)]
            with np.load(old/(entry['id']+'-full-world-features.npz'),allow_pickle=False) as a:times=a['timestamps'].copy();points=a['points'].copy()
            actual=[];boxes=[];nearest=[]
            for column,(stamp,rgb) in enumerate(decode(entry,desired)):
                pose_index=int(np.argmin(abs(times-stamp)))
                if abs(int(times[pose_index])-stamp)>100 or stamp>int(stamps[-1]):raise ValueError('实际帧与姿态/语义窗口不匹配')
                patch,box=upper(rgb,points[pose_index]);picture=Image.fromarray(patch);picture.thumbnail((352,238));x=column*360;y=line*290
                draw.text((x+4,y+4),str(page*6+line+1)+' '+entry['id'],fill='black');draw.text((x+4,y+21),str(stamp)+'ms / raw '+str(patch.shape[1])+'x'+str(patch.shape[0]),fill='black')
                sheet.paste(picture,(x+4,y+43));actual.append(stamp);boxes.append(box);nearest.append(int(times[pose_index]))
            reviews.append({k:entry[k] for k in ('id','source_sha256','path','partition','group','start_ms','end_ms','crop')}|{'semantic_cache_sha256':sha(semantic),
                'semantic_timestamps_ms':[int(t) for t in stamps],'actual_timestamps_ms':actual,'nearest_pose_timestamps_ms':nearest,'pixel_crops':boxes,'sheet':page+1,'row':line+1})
        target=out/('curl-validation-grips-'+str(page+1)+'.jpg');sheet.save(target,quality=94);sheets.append(target.relative_to(ROOT).as_posix())
    atomic_json(out/'curl-validation-grip-frames.json',{'schema_version':1,'purpose':'all original generic-curl validation inputs; exact-window developer visual review, not independent technique truth',
        'index_sha256':sha(index),'script_sha256':sha(Path(__file__)),'model_predictions_shown':False,'new_model_inferences':0,'rows':reviews,'sheets':sheets})
    print(json.dumps({'inputs':len(reviews),'sheets':sheets,'new_model_inferences':0}),flush=True)


if __name__=='__main__':main()
