"""生成本地视频接触表，供逐段核实动作；不会自动把整段教程标为训练动作。"""
import argparse,json,sys,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
import av
from PIL import Image,ImageDraw

OUT=ROOT/'data/verification/step-22'


def review(entries,target,times=None):
    columns=min(len(times),5) if times else 5
    rows_per_entry=math.ceil(len(times)/columns) if times else 1
    sheet=Image.new('RGB',(columns*300,len(entries)*rows_per_entry*196),'white');draw=ImageDraw.Draw(sheet)
    for row,entry in enumerate(entries):
        seconds=entry.get('review_times') or times or [entry['duration_seconds']*fraction for fraction in (.15,.3,.45,.6,.75)]
        with av.open(str(project_path(entry['path']))) as video:
            for col,sec in enumerate(seconds):
                video.seek(round(sec*1000000))
                frame=next((f for f in video.decode(video=0) if f.time is not None and f.time>=sec),None)
                if frame is None:continue
                picture=frame.to_image();picture.thumbnail((300,166))
                x,y=(col%columns)*300,(row*rows_per_entry+col//columns)*196
                sheet.paste(picture,(x,y+28))
                draw.text((x+3,y+2),f"{entry['id']}  {sec:.1f}s",fill='black')
                draw.text((x+3,y+14),entry.get('action_id',entry.get('action','')),fill='black')
    sheet.save(project_path(target))
    print(str(target.relative_to(ROOT)))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ids');parser.add_argument('--times');parser.add_argument('--batch',type=int)
    args=parser.parse_args()
    entries=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']
    if args.ids:
        ids=args.ids.split(',');entries=[next(e for e in entries if e['id']==key) for key in ids]
        review(entries,OUT/(entries[0]['id']+'-review.jpg'),[float(t) for t in args.times.split(',')] if args.times else None)
    else:
        entries=[e for e in entries if e['id'].startswith('youtube-')]
        batches=range((len(entries)+5)//6) if args.batch is None else [args.batch]
        for batch in batches:review(entries[batch*6:batch*6+6],OUT/f'cc-segments-review-{batch}.jpg')
