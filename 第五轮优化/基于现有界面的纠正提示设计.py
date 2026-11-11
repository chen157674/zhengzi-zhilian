"""使用现有前端截图与项目演示素材，在本地合成局部提示设计；不调用模型、不改应用。"""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont,ImageOps
import cv2,json,io,runpy
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
UI=ROOT/'UI图'
SRC=PROJECT/'第四轮优化/实现核对'
FONT=Path(r'C:\Windows\Fonts\msyh.ttc')
BOLD=Path(r'C:\Windows\Fonts\msyhbd.ttc')
def font(n,b=False):return ImageFont.truetype(str(BOLD if b else FONT),n)
def check(path):
 for p in (path,*path.parents):
  if p.is_symlink() or p.is_junction():raise RuntimeError('路径含链接')
 if not path.resolve().is_relative_to(PROJECT.resolve()):raise RuntimeError('路径越界')
def wrap(text,width,f):
 lines=[];line=''
 for c in text:
  if line and f.getlength(line+c)>width:lines.append(line);line=c
  else:line+=c
 if line:lines.append(line)
 return lines

def cue(im,box,title='身体向右侧持续倾斜',instruction='保持躯干稳定，减少随推举产生的左右摆动。',neutral=False):
 x,y,w,h=box;layer=Image.new('RGBA',im.size,(0,0,0,0));d=ImageDraw.Draw(layer)
 d.rounded_rectangle((x,y,x+w,y+h),radius=14,fill=(25,32,33,240),outline=(255,255,255,70),width=1)
 d.rounded_rectangle((x,y+12,x+4,y+h-12),radius=2,fill='#E88B42' if not neutral else '#A8B3B1')
 d.text((x+23,y+16),'动作纠正提示',font=font(21,True),fill='#FFFFFF')
 d.text((x+23,y+52),title,font=font(22,True),fill='#FFFFFF')
 if not neutral:d.text((x+23,y+88),'下一次这样做',font=font(15,True),fill='#FFC18A')
 ty=y+113 if not neutral else y+88
 for line in wrap(instruction,w-46,font(17)):
  d.text((x+23,ty),line,font=font(17),fill='#F5F2EC');ty+=26
 if not neutral:d.text((x+23,y+h-27),'关注部位：躯干',font=font(14),fill='#D8E2DE')
 im.paste(Image.alpha_composite(im.convert('RGBA'),layer).convert('RGB'))

def preview(im,path):
 copy=im.copy();copy.thumbnail((1200,1400))
 for q in (86,80,74,68,62,56,50):
  b=io.BytesIO();copy.save(b,format='JPEG',quality=q,optimize=True)
  if len(b.getvalue())<=256*1024:break
 if len(b.getvalue())>256*1024:raise RuntimeError('预览超预算')
 path.write_bytes(b.getvalue())

def save(im,name):
 p=UI/name;check(p);im.save(p,optimize=True);preview(im,p.with_name(p.stem+'.preview.jpg'))
 return dict(file=name,width=im.width,height=im.height,bytes=p.stat().st_size)

def live():
 original=PROJECT/'OPC专项赛/作品截图/03_实时分析.png'
 im=Image.open(original).convert('RGB')
 # Keep the existing 1440x1080 page, its 1200x560 stage, toolbar and footer unchanged.
 box=(120,355,1320,915);cw,ch=1200,560
 cap=cv2.VideoCapture(str(PROJECT/'data/datasets/user-demos/user-1694.mp4'))
 cap.set(cv2.CAP_PROP_POS_MSEC,4000);ok,frame=cap.read();cap.release()
 if not ok:raise RuntimeError('原演示视频解码失败')
 photo=Image.fromarray(cv2.cvtColor(frame,cv2.COLOR_BGR2RGB))
 photo=ImageOps.mirror(photo) # Original reference screen has mirror enabled.
 fit=ImageOps.contain(photo,(cw,ch),Image.Resampling.LANCZOS)
 stage=Image.new('RGB',(cw,ch),'#F3EAE1');ox=(cw-fit.width)//2;oy=(ch-fit.height)//2
 stage.paste(fit,(ox,oy))
 # Same-source existing pose track, same selected time; this remains a design reference, not a new test.
 track=json.loads((PROJECT/'fitness_app/static/demo/user-1694-track.json').read_text(encoding='utf-8'))
 nearest=min(track['frames'],key=lambda f:abs(f['t']-4000))
 pts=nearest.get('p',[]);d=ImageDraw.Draw(stage)
 def point(i):
  if i>=len(pts) or not isinstance(pts[i],list) or len(pts[i])<2:return None
  x,y=pts[i][:2]
  if x is None or y is None or not 0<=x<=1 or not 0<=y<=1:return None
  return (ox+(1-x)*fit.width,oy+y*fit.height)
 for a,b in [(11,12),(11,13),(13,15),(12,14),(14,16),(11,23),(12,24),(23,24)]:
  pa,pb=point(a),point(b)
  if pa and pb:d.line((pa,pb),fill='#36C4AE',width=3)
 for i in [11,12,13,14,15,16,23,24]:
  v=point(i)
  if v:d.ellipse((v[0]-4,v[1]-4,v[0]+4,v[1]+4),fill='#36C4AE',outline='white',width=1)
 mask=Image.new('L',(cw,ch));ImageDraw.Draw(mask).rounded_rectangle((0,0,cw-1,ch-1),radius=12,fill=255)
 im.paste(stage,box[:2],mask)
 d=ImageDraw.Draw(im)
 d.rounded_rectangle((138,371,365,412),radius=8,fill='#25302F')
 d.text((151,382),'自动识别 · 器械推胸',font=font(15,True),fill='white')
 # Preserve toolbar positions, replace only permission-state controls with their running-state variants.
 d.rounded_rectangle((1024,273,1159,323),radius=11,fill='#B9431C')
 d.text((1091,298),'暂停分析',font=font(17,True),fill='white',anchor='mm')
 d.rounded_rectangle((1174,273,1307,323),radius=11,fill='#FFFCF8',outline='#B9431C',width=1)
 d.text((1241,298),'结束训练',font=font(17),fill='#B9431C',anchor='mm')
 d.rounded_rectangle((1256,372,1298,414),radius=8,fill='#25302F')
 for poly in [[(1266,389),(1266,382),(1273,382)],[(1282,382),(1289,382),(1289,389)],[(1266,397),(1266,404),(1273,404)],[(1282,404),(1289,404),(1289,397)]]:d.line(poly,fill='white',width=2)
 cue(im,(140,704,515,194))
 d=ImageDraw.Draw(im);d.text((120,1020),'基于现有前端的局部布局示意 · 原画面与原数据轨迹在本地复用',font=font(14),fill='#78675B')
 return im,original

def video():
 original=SRC/'视频分析结果-演示.png';im=Image.open(original).convert('RGB');d=ImageDraw.Draw(im)
 # Original video, page title, lower action-feedback cards and bottom buttons remain untouched.
 d.rounded_rectangle((850,148,1248,593),radius=14,fill='#FFFCF8')
 d.text((873,174),'动作纠正提示',font=font(23,True),fill='#402517')
 d.text((873,211),'需要调整',font=font(15,True),fill='#B9431C')
 d.text((873,239),'身体向右侧持续倾斜',font=font(20,True),fill='#402517')
 d.rounded_rectangle((871,279,1227,380),radius=10,fill='#FFF0DF')
 d.text((888,291),'下一次这样做',font=font(16,True),fill='#B9431C')
 for i,line in enumerate(['保持躯干稳定，','减少随推举产生的左右摆动。']):d.text((888,320+24*i),line,font=font(16),fill='#402517')
 d.text((873,396),'关注部位：躯干 · 00:15—00:16',font=font(14),fill='#78675B')
 d.rounded_rectangle((872,427,1227,474),radius=10,fill='#B9431C')
 d.text((1049,450),'定位回看',font=font(17,True),fill='white',anchor='mm')
 d.text((1049,494),'问AI教练：为什么会这样？',font=font(14),fill='#B9431C',anchor='mm')
 with Image.open(original) as src:im.paste(src.convert('RGB').crop((850,514,1248,593)),(850,514))
 d=ImageDraw.Draw(im);d.text((32,906),'基于现有前端的局部布局示意 · 原视频与原反馈列表保留',font=font(13),fill='#78675B')
 return im,original


def insert_space(im,y,height):
 out=Image.new('RGB',(im.width,im.height+height),'#FFF9F1')
 out.paste(im.crop((0,0,im.width,y)),(0,0));out.paste(im.crop((0,y,im.width,im.height)),(0,y+height));return out

def note(im,text='基于现有前端的局部布局示意 · 不是本次运行或算法验收结果'):
 d=ImageDraw.Draw(im);d.text((18,im.height-20),text,font=font(11 if im.width<700 else 13),fill='#78675B')
 return im

def prepare():
 source=PROJECT/'OPC专项赛/作品截图/03_实时分析.png'
 return note(Image.open(source).convert('RGB'),'沿用现有准备页：开启前不显示虚构纠正问题'),source

def record():
 source=PROJECT/'OPC专项赛/作品截图/07_动作反馈.png';old=Image.open(source).convert('RGB')
 # Grow only the original focus/advice strip; preserve the rest of the record page.
 out=insert_space(old,426,104);d=ImageDraw.Draw(out)
 d.rounded_rectangle((112,365,1328,526),radius=12,fill='#FFF0DF',outline='#E5CDB6')
 d.text((132,382),'动作纠正提示',font=font(21,True),fill='#402517')
 d.text((132,417),'本次关注：躯干稳定与回程控制',font=font(21,True),fill='#402517')
 d.text((132,458),'下一次这样做：减少前后借力，保持躯干稳定，控制器械返回。',font=font(18),fill='#B9431C')
 d.text((132,494),'按原记录查看具体判项；未保存原片时不伪造视频定位入口。',font=font(14),fill='#78675B')
 d.text((1176,445),'查看动作明细 →',font=font(16),fill='#B9431C')
 return note(out),source

def mobile_live(paused=False):
 source=SRC/('实时相机中断-窄屏.png' if paused else '实时准备-窄屏.png')
 old=Image.open(source).convert('RGB')
 if not paused:
  live_im,_=live();camera=live_im.crop((120,355,1320,915))
  # Use the same source image before the enlarged cue, so the phone keeps an unobscured full-width stage.
  cap=cv2.VideoCapture(str(PROJECT/'data/datasets/user-demos/user-1694.mp4'));cap.set(cv2.CAP_PROP_POS_MSEC,4000);ok,f=cap.read();cap.release()
  if not ok:raise RuntimeError('手机演示帧解码失败')
  photo=ImageOps.mirror(Image.fromarray(cv2.cvtColor(f,cv2.COLOR_BGR2RGB)))
  stage=Image.new('RGB',(358,268),'#F3EAE1');fit=ImageOps.contain(photo,stage.size,Image.Resampling.LANCZOS);dx=(358-fit.width)//2;dy=(268-fit.height)//2;stage.paste(fit,(dx,dy))
  td=json.loads((PROJECT/'fitness_app/static/demo/user-1694-track.json').read_text(encoding='utf-8'));frame_track=min(td['frames'],key=lambda f:abs(f['t']-4000));ps=frame_track.get('p',[]);sd=ImageDraw.Draw(stage)
  def pt(i):
   if i>=len(ps) or not isinstance(ps[i],list) or len(ps[i])<2 or ps[i][0] is None or ps[i][1] is None:return None
   x,y=ps[i][:2]
   return (dx+(1-x)*fit.width,dy+y*fit.height) if 0<=x<=1 and 0<=y<=1 else None
  for a,b in [(11,12),(11,13),(13,15),(12,14),(14,16),(11,23),(12,24),(23,24)]:
   pa,pb=pt(a),pt(b)
   if pa and pb:sd.line((pa,pb),fill='#36C4AE',width=2)
  mask=Image.new('L',stage.size);ImageDraw.Draw(mask).rounded_rectangle((0,0,357,267),radius=10,fill=255);old.paste(stage,(16,385),mask)
  d=ImageDraw.Draw(old);d.rounded_rectangle((29,398,228,431),radius=7,fill='#25302F');d.text((39,407),'自动识别 · 器械推胸',font=font(13),fill='white')
  d.rounded_rectangle((28,311,190,358),radius=8,fill='#B9431C');d.text((109,335),'暂停分析',font=font(15,True),fill='white',anchor='mm')
  d.rounded_rectangle((204,311,362,358),radius=8,fill='#FFFCF8',outline='#B9431C');d.text((283,335),'结束训练',font=font(15),fill='#B9431C',anchor='mm')
 out=insert_space(old,653,195);d=ImageDraw.Draw(out)
 d.rounded_rectangle((16,662,374,835),radius=11,fill='#25302F')
 d.text((31,675),'动作纠正提示',font=font(19,True),fill='white')
 if paused:
  title='已暂停 · 等待新的画面';lines=['恢复后重新观察，','旧的纠正提示已撤回。']
 else:
  title='身体向右侧持续倾斜';lines=['保持躯干稳定，减少随推举','产生的左右摆动。']
 d.text((31,708),title,font=font(17,True),fill='white')
 d.text((31,740),'当前状态' if paused else '下一次这样做',font=font(14,True),fill='#FFC18A')
 for i,line in enumerate(lines):d.text((31,765+22*i),line,font=font(14),fill='#F5F2EC')
 # Keep the existing control layout and update only the legacy brand name.
 d.rectangle((17,5,205,56),fill='#FFFCF8');d.text((24,15),'知练同行',font=font(25,True),fill='#402517')
 return out,source

def mobile_video():
 source=SRC/'视频结果-窄屏.png';old=Image.open(source).convert('RGB');out=insert_space(old,633,135);d=ImageDraw.Draw(out)
 d.rounded_rectangle((16,355,374,768),radius=14,fill='#FFFCF8')
 d.text((35,375),'动作纠正提示',font=font(21,True),fill='#402517')
 d.text((35,416),'身体向右侧持续倾斜',font=font(18,True),fill='#402517')
 d.rounded_rectangle((33,452,357,555),radius=10,fill='#FFF0DF');d.text((47,464),'下一次这样做',font=font(15,True),fill='#B9431C')
 for i,line in enumerate(['保持躯干稳定，','减少随推举产生的左右摆动。']):d.text((47,492+22*i),line,font=font(14),fill='#402517')
 d.text((35,572),'关注部位：躯干 · 00:15—00:16',font=font(13),fill='#78675B')
 d.rounded_rectangle((34,607,356,650),radius=9,fill='#B9431C');d.text((195,628),'定位回看',font=font(16,True),fill='white',anchor='mm')
 d.text((195,677),'问AI教练：为什么会这样？',font=font(14),fill='#B9431C',anchor='mm')
 # Existing record/reanalyse actions remain and move with the expanded feedback block.
 out.paste(old.crop((28,560,350,625)),(28,696))
 return out,source

def mobile_record():
 source=SRC/'训练记录详情-窄屏.png';old=Image.open(source).convert('RGB');out=insert_space(old,238,170);d=ImageDraw.Draw(out)
 d.rounded_rectangle((16,246,374,399),radius=10,fill='#FFF0DF',outline='#E5CDB6')
 d.text((30,258),'动作纠正提示',font=font(19,True),fill='#402517')
 d.text((30,290),'本次调整建议',font=font(17,True),fill='#402517')
 for i,line in enumerate(['保持躯干稳定，','按原记录建议控制动作回程。']):d.text((30,321+22*i),line,font=font(14),fill='#B9431C')
 d.text((30,375),'查看动作明细 →',font=font(13),fill='#B9431C')
 return out,source

def failed():
 source=SRC/'记录保存失败.png'
 return note(Image.open(source).convert('RGB'),'沿用现有保存失败页：先恢复保存，不生成新的纠正结论'),source

def states():
 out=Image.new('RGB',(1440,640),'#FFF9F1');d=ImageDraw.Draw(out)
 d.text((48,34),'现有纠正提示组件 · 状态设计',font=font(28,True),fill='#402517')
 for i,label in enumerate(['形成有效问题','观察不足','暂停或画面中断']):d.text((48+465*i,114),label,font=font(20,True),fill='#402517')
 cue(out,(46,166,438,230))
 cue(out,(511,166,438,230),'正在观察','暂未形成可靠结论，不显示具体动作错误。',True)
 cue(out,(976,166,418,230),'已暂停','等待新画面，旧提示已撤回。',True)
 d=ImageDraw.Draw(out);d.text((48,446),'准备、观察不足和暂停不是动作错误；未发现已支持项目偏差也不代表整体动作正确。',font=font(18),fill='#78675B')
 d.text((48,508),'沿用现有深色提示组件，不增加右侧固定栏，不改变大画面布局。',font=font(18),fill='#78675B')
 return out,SRC/'实时分析-按图核对.png'

def missing_source(mobile=False):
 """保留原结果主体，仅将原片与所有定位入口切换到未匹配状态。"""
 source=UI/('47_视频结果手机.png' if mobile else '29_视频分析结果.png')
 check(source)
 with Image.open(source) as original:im=original.convert('RGB')
 if mobile:
  # The old long screenshot's fixed navigation crossed the second feedback card.
  # Recreate only this lower feedback block and put the existing shared nav after it.
  old=im;im=Image.new('RGB',(390,1308),'#FFF9F1')
  im.paste(old.crop((0,0,390,795)),(0,0));d=ImageDraw.Draw(im)
  d.rounded_rectangle((18,140,372,340),radius=12,fill='#F3EAE1',outline='#E0D5CB')
  d.text((195,166),'原片未加载',font=font(21,True),fill='#402517',anchor='mt')
  d.text((195,203),'纠正提示和反馈已保存，可继续查看。',font=font(13),fill='#78675B',anchor='mt')
  d.text((195,226),'选回同一原片后，再定位回看。',font=font(13),fill='#78675B',anchor='mt')
  d.rounded_rectangle((77,256,313,300),radius=9,fill='#B9431C')
  d.text((195,278),'重新选择原片',font=font(16,True),fill='white',anchor='mm')
  d.text((195,313),'匹配成功后恢复回看，无需重新分析。',font=font(11),fill='#78675B',anchor='mt')
  d.rounded_rectangle((34,607,356,650),radius=9,fill='#E7E0D8')
  d.text((195,628),'定位回看 · 待匹配原片',font=font(15,True),fill='#776E65',anchor='mm')
  d.rounded_rectangle((18,795,372,1170),radius=14,fill='#FFFCF8',outline='#E8DDD2')
  d.text((35,815),'动作反馈',font=font(20,True),fill='#402517')
  d.text((351,824),'2处反馈',font=font(13),fill='#78675B',anchor='rt')
  d.text((35,848),'已保存的反馈保留；定位需先匹配原片。',font=font(12),fill='#78675B')
  rows=[(878,'左侧回程节奏明显快于用力阶段','0:04 — 0:05','平稳控制回程。'),
        (1017,'身体向右侧持续倾斜','0:15 — 0:16','保持躯干稳定，减少左右摆动。')]
  for y,title,stamp,advice in rows:
   d.rounded_rectangle((35,y,355,y+123),radius=10,fill='#F5EDE3',outline='#DFD1C3')
   for j,line in enumerate(wrap(title,206,font(15,True))):
    d.text((49,y+13+23*j),line,font=font(15,True),fill='#402517')
   d.text((341,y+16),'待匹配原片',font=font(11),fill='#78675B',anchor='rt')
   d.text((49,y+69),stamp,font=font(13),fill='#78675B')
   d.text((49,y+94),advice,font=font(12),fill='#78675B')
  d.text((195,1189),'原片匹配不会重新运行分析',font=font(12),fill='#78675B',anchor='mt')
  runpy.run_path(str(ROOT/'生成与核对UI.py'))['mobile_nav'](im,'video')
 else:
  d=ImageDraw.Draw(im)
  d.rounded_rectangle((32,148,827,596),radius=14,fill='#F3EAE1',outline='#E0D5CB')
  d.rounded_rectangle((974,88,1248,124),radius=18,fill='#FFF0DF')
  d.text((1111,106),'已保存结果 · 待匹配原片',font=font(14),fill='#78675B',anchor='mm')
  d.rounded_rectangle((405,241,455,277),radius=7,outline='#9D8978',width=2)
  d.polygon([(425,250),(425,268),(439,259)],outline='#9D8978',width=2)
  d.text((430,302),'原片未加载',font=font(28,True),fill='#402517',anchor='mt')
  d.text((430,351),'已保存的纠正提示和反馈仍可查看。',font=font(18),fill='#78675B',anchor='mt')
  d.text((430,383),'重新选择本次分析的原视频后，再定位回看。',font=font(17),fill='#78675B',anchor='mt')
  d.rounded_rectangle((310,438,550,488),radius=10,fill='#B9431C')
  d.text((430,463),'重新选择原片',font=font(18,True),fill='white',anchor='mm')
  d.text((430,511),'匹配成功后恢复回看，不会重新运行分析。',font=font(14),fill='#78675B',anchor='mt')
  d.rounded_rectangle((872,427,1227,474),radius=10,fill='#E7E0D8')
  d.text((1049,450),'定位回看 · 待匹配原片',font=font(17,True),fill='#776E65',anchor='mm')
  for x in (533,1124):
   bg=im.getpixel((x,734))
   d.rectangle((x,729,x+94,759),fill=bg)
   d.text((x+47,744),'待匹配原片',font=font(13),fill='#78675B',anchor='mm')
  d.rectangle((24,900,1256,935),fill=im.getpixel((24,901)))
  d.text((32,906),'基于现有结果页的缺片状态设计 · 示例数据 · 不是运行或算法验收截图',font=font(13),fill='#78675B')
 return im,source

if __name__=='__main__':
 import argparse
 parser=argparse.ArgumentParser()
 parser.add_argument('--missing-source-only',action='store_true',help='仅补齐原片缺失桌面和手机稿，不重绘其他页')
 args=parser.parse_args()
 result=[]
 jobs=[(missing_source,'56_原片缺失.png'),(lambda:missing_source(True),'56_原片缺失_手机.png')]
 if not args.missing_source_only:
  jobs=[(live,'26_实时分析.png'),(video,'29_视频分析结果.png'),(prepare,'25_实时分析准备.png'),(record,'31_训练详情.png'),(mobile_live,'46_实时分析手机.png'),(mobile_video,'47_视频结果手机.png'),(lambda:mobile_live(True),'54_相机中断.png'),(failed,'55_保存失败.png'),(states,'58_实时反馈状态.png'),(mobile_record,'59_训练详情手机.png')]+jobs
 for factory,name in jobs:
  im,original=factory();meta=save(im,name);meta['source']=str(original.relative_to(PROJECT));result.append(meta)
 print(json.dumps(result,ensure_ascii=False))
