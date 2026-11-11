"""第五轮设计图的顺序生成、既有页面复用和覆盖核对；不修改应用。"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json, subprocess, sys, time

ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
UI=ROOT/'UI图'
MANIFEST=ROOT/'页面设计清单.json'
HELPER=Path(r'E:\07_Projects\90_KnowledgeBase\AI工具维护\ccswitch维护\内置生图兼容入口.py')
FONT=Path(r'C:\Windows\Fonts\msyh.ttc')
SOURCE=PROJECT/'OPC专项赛/作品截图/01_首页.png'
LABELS=[('home','首页'),('realtime','实时分析'),('video','视频分析'),('training','我的训练'),('guide','健身指南')]

def safe(p):
    p=Path(p)
    if not p.is_absolute(): p=PROJECT/p
    for q in (p,*p.parents):
        if q.is_symlink() or q.is_junction():raise RuntimeError('路径含链接')
    p=p.resolve()
    if not p.is_relative_to(PROJECT.resolve()):raise RuntimeError('越出项目边界')
    return p

def save(data):
    MANIFEST.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def font(size,bold=False):
    fp=Path(r'C:\Windows\Fonts\msyhbd.ttc') if bold else FONT
    return ImageFont.truetype(str(fp),max(10,int(size)))

def shared_header(width,active):
    h=round(width*80/1440)
    im=Image.new('RGB',(width,h),'#FFFCF8');d=ImageDraw.Draw(im)
    scale=width/1440
    with Image.open(SOURCE) as source:
        logo=source.convert('RGB').crop((40,7,260,66)).resize((round(220*scale),round(59*scale)),Image.Resampling.LANCZOS)
    im.paste(logo,(round(38*scale),round(7*scale)))
    for (key,label),x in zip(LABELS,[520,635,750,865,980]):
        d.text((x*scale,39*scale),label,font=font(16*scale),fill='#B9431C' if key==active else '#402517',anchor='mm')
        if key==active:d.line(((x-30)*scale,66*scale,(x+30)*scale,66*scale),fill='#B9431C',width=max(1,round(2*scale)))
    x=width-round(68*scale)
    d.ellipse((x-15*scale,23*scale,x+15*scale,53*scale),fill='#EEDCCB')
    d.text((width-101*scale,39*scale),'用户',font=font(13*scale),fill='#78675B',anchor='rm')
    d.line((0,h-1,width,h-1),fill='#EEDCCB')
    return im

def mobile_nav(im,active):
    d=ImageDraw.Draw(im);w,h=im.size;bar=min(82,round(w*82/390));top=h-bar
    d.rectangle((0,top,w,h),fill='#FFFCF8');d.line((0,top,w,top),fill='#EEDCCB')
    for i,(key,label) in enumerate(LABELS):
        x=(i+.5)*w/5;color='#B9431C' if key==active else '#78675B'
        if key==active:d.rounded_rectangle((x-21,top+8,x+21,top+38),radius=8,fill='#FFF0DF')
        y=top+20
        if i==0:
            d.line((x-8,y,x,y-7,x+8,y),fill=color,width=2);d.rectangle((x-6,y,x+6,y+8),outline=color,width=2)
        elif i==1:
            d.rounded_rectangle((x-9,y-5,x+9,y+8),radius=3,outline=color,width=2);d.ellipse((x-3,y-3,x+3,y+4),outline=color,width=1)
        elif i==2:
            d.rectangle((x-9,y-6,x+9,y+8),outline=color,width=2);d.polygon([(x-2,y-3),(x+4,y+1),(x-2,y+5)],fill=color)
        elif i==3:
            d.rectangle((x-7,y-7,x+7,y+9),outline=color,width=2)
            for yy in (y-2,y+2,y+6):d.line((x-3,yy,x+4,yy),fill=color,width=1)
        else:
            d.line((x,y-6,x,y+8),fill=color,width=1);d.line((x-9,y-6,x-1,y-5,x,y+8,x+1,y-5,x+9,y-6,x+9,y+7,x+1,y+8,x,y+8,x-9,y+7,x-9,y-6),fill=color,width=1)
        d.text((x,top+53),label,font=font(11),fill=color,anchor='mm')


def auth_branding(im, register=False):
    d=ImageDraw.Draw(im)
    with Image.open(SOURCE) as src:
        logo=src.convert('RGBA').crop((40,7,260,66))
    pixels=[]
    for r,g,b,a in logo.getdata():
        pixels.append((r,g,b,0 if min(r,g,b)>238 else 255))
    logo.putdata(pixels)
    # Preserve the original shared illustration and form geometry; replace legacy brand words only.
    d.rectangle((40,18,285,84),fill=im.getpixel((39,18)));im.paste(logo,(40,21),logo)
    if register:
        d.rectangle((870,160,1240,236),fill=im.getpixel((880,160)));im.paste(logo,(876,168),logo)
        d.rectangle((870,250,1260,307),fill=im.getpixel((880,250)))
        d.text((876,262),'创建知练同行账号',font=font(28,True),fill='#402517')
    else:
        d.rectangle((870,204,1240,282),fill=im.getpixel((880,204)));im.paste(logo,(876,213),logo)
        d.rectangle((870,296,1260,354),fill=im.getpixel((880,296)))
        d.text((876,306),'登录知练同行',font=font(28,True),fill='#402517')
    return im

def reuse(item):
    target=safe(UI/item['file'])
    source=safe(item['source'])
    with Image.open(source) as original:im=original.convert('RGB')
    w,h=im.size
    if item.get('active')=='auth':
        if im.width>700:im=auth_branding(im,register='注册' in item['title'])
    elif item.get('overlay_only'):
        pass
    elif item.get('mobile'):
        # The source is an existing responsive screen; only shared app furniture is updated.
        d=ImageDraw.Draw(im);d.rectangle((0,0,w,54),fill='#FFFCF8')
        d.text((16,27),'知练同行',font=font(20,True),fill='#402517',anchor='lm')
        d.ellipse((w-39,17,w-17,39),fill='#EEDCCB')
        mobile_nav(im,item.get('active'))
    else:
        header=shared_header(w,item.get('active'));header_h=header.height
        # Preserve page body and insert the shared training sub-navigation for record pages.
        if item.get('active')=='training':
            sh=round(w*50/1440);updated=Image.new('RGB',(w,h+sh),'#FFF9F1')
            updated.paste(header,(0,0));updated.paste(im.crop((0,header_h,w,h)),(0,header_h+sh))
            d=ImageDraw.Draw(updated)
            for i,label in enumerate(['训练计划','训练记录','训练回顾']):
                xx=round((72+i*132)*w/1440);yy=header_h+sh//2
                d.text((xx,yy),label,font=font(15*w/1440),fill='#B9431C' if i==1 else '#78675B',anchor='lm')
                if i==1:d.line((xx,header_h+sh-3,xx+70*w/1440,header_h+sh-3),fill='#B9431C',width=2)
            im=updated
        else:im.paste(header,(0,0))
    d=ImageDraw.Draw(im)
    if im.width>700 and im.height>=950 and item.get('active')!='auth' and not item.get('overlay_only'):
        footer_bg=im.getpixel((10,im.height-1))
        d.rectangle((0,im.height-35,im.width,im.height),fill=footer_bg)
        d.text((im.width//2,im.height-14),'知练同行 · 看清动作，练好下一组。',font=font(12),fill='#78675B',anchor='mm')
    if im.width>700 and item.get('active')!='auth' and not item.get('overlay_only'):
        d.text((im.width-18,im.height-12),'UI方案 · 既有设计复用',font=font(12),fill='#78675B',anchor='rs')
    im.save(target,optimize=True)
    item.update(status='ready',review='已保留既有主体并更新共用导航；待联系表检查',width=im.width,height=im.height,bytes=target.stat().st_size)

def generate(data,item):
    target=safe(UI/item['file'])
    cmd=[sys.executable,'-B',str(HELPER),'--project-root',str(PROJECT),'--output',str(target),'--timeout','600']
    if target.exists():cmd.append('--replace')
    started=time.monotonic()
    completed=subprocess.run(cmd,input=data['base_prompt']+'\n'+item['prompt'],text=True,encoding='utf-8',cwd=PROJECT,capture_output=True)
    result=None
    for line in completed.stdout.splitlines():
        if line.startswith('RESULT_JSON:'):
            result=json.loads(line[len('RESULT_JSON:'):])
    if completed.returncode or not result:
        # The trusted helper redacts relay credentials. Keep only bounded diagnostics.
        message=(completed.stderr+'\n'+completed.stdout)[-2500:]
        item.update(status='failed',error=message,elapsed_seconds=round(time.monotonic()-started,1))
        print('FAILED '+item['file']+' '+message[:350],flush=True)
        return
    with Image.open(target) as im:im.verify()
    item.update(status='ready',review='PNG校验通过，待联系表检查',width=result['width'],height=result['height'],bytes=result['bytes'],elapsed_seconds=round(time.monotonic()-started,1))
    item.pop('error',None)
    print('DONE '+item['file']+' '+str(result['width'])+'x'+str(result['height']),flush=True)


def build_index(data):
    import html, io, math
    from urllib.parse import quote
    missing=[x['file'] for x in data['items'] if x['status'] not in ('ready','approved') or not (UI/x['file']).is_file()]
    if missing:raise RuntimeError('仍有未完成设计：'+','.join(missing))
    output=UI/'页面联系表.jpg'
    command=[sys.executable,'-B',str(HELPER),'--project-root',str(PROJECT),'--review','--output',str(output)]
    if list(UI.glob('页面联系表*.jpg')):command.append('--replace')
    for item in data['items']:command+=['--image',str(UI/item['file'])]
    result=subprocess.run(command,text=True,encoding='utf-8',capture_output=True,cwd=PROJECT)
    if result.returncode:raise RuntimeError((result.stderr+result.stdout)[-2000:])
    reports=[json.loads(l[len('RESULT_JSON:'):]) for l in result.stdout.splitlines() if l.startswith('RESULT_JSON:')]
    if len(reports)!=1:raise RuntimeError('没有唯一联系表结果')
    sheets=[Path(x) for x in reports[0]['files']];sizes=[]
    for path in sheets:
        with Image.open(path) as im:
            rgb=im.convert('RGB')
            for quality in (86,80,74,68,62,56,50,44,38,32):
                buf=io.BytesIO();rgb.save(buf,format='JPEG',quality=quality,optimize=True)
                if len(buf.getvalue())<=80*1024:break
            if len(buf.getvalue())>80*1024:raise RuntimeError('单张联系表仍超过预算')
            path.write_bytes(buf.getvalue());sizes.append(path.stat().st_size)
    if sum(sizes)>1024*1024:raise RuntimeError('整套联系表超过本轮1MiB预算')
    groups=list(dict.fromkeys(x['group'] for x in data['items']))
    filters='<button class="selected" data-group="">全部</button>'+''.join('<button data-group="'+html.escape(g,quote=True)+'">'+html.escape(g)+'</button>' for g in groups)
    cards=[]
    for i,item in enumerate(data['items']):
        sheet_index=i//6;within=i%6;selected=data['items'][sheet_index*6:(sheet_index+1)*6]
        cols=min(3,len(selected));rows=math.ceil(len(selected)/cols)
        xx=0 if cols==1 else (within%cols)*100/(cols-1);yy=0 if rows==1 else (within//cols)*100/(rows-1)
        image_url=quote('UI图/'+item['file']);thumb_url=quote('UI图/'+sheets[sheet_index].name)
        thumb=f"background-image:url('{thumb_url}');background-size:{cols*100}% {rows*100}%;background-position:{xx}% {yy}%"
        label=('已确认' if item['kind']=='approved' or item.get('revision_status')=='approved' else '现有界面局部合成' if item.get('revision_method')=='existing_frontend_local_composite' else '生成重绘' if item.get('revision_method')=='native_imagegen' else {'generate':'生成设计','reuse':'既有设计复用'}[item['kind']])
        review=html.escape(item.get('review','待审阅'))
        title=html.escape(item['title']);group=html.escape(item['group'],quote=True)
        variants=''
        for variant in item.get('responsive_files',[]):
            variant_path=safe(UI/variant['file'])
            if not variant_path.is_file():raise RuntimeError('缺少响应式图稿：'+variant['file'])
            variants+='<p><a href="'+quote('UI图/'+variant['file'])+'" target="_blank">'+html.escape(variant.get('title','手机图'))+' ↗</a></p>'
        cards.append('<article data-group="'+group+'" data-search="'+html.escape(item['title']+' '+item['file'],quote=True)+'"><a class="shot" style="'+thumb+'" href="'+image_url+'" target="_blank" aria-label="查看'+title+'完整图"></a><div class="caption"><div><small>'+html.escape(item['group'])+' · '+label+'</small><h2>'+title+'</h2><p>'+html.escape(item['route'] or item['coverage'])+'</p><p>'+review+'</p>'+variants+'</div><a href="'+image_url+'" target="_blank">完整图 ↗</a></div></article>')
    page='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>知练同行 · 全页面UI设计</title><style>
    *{box-sizing:border-box}body{margin:0;background:#fff9f1;color:#402517;font:16px/1.6 "Microsoft YaHei",system-ui,sans-serif}header{max-width:1400px;margin:auto;padding:36px 28px 20px}h1{font-size:30px;margin:0 0 8px}header p{margin:0;color:#78675b}.tools{position:sticky;top:0;z-index:2;background:#fff9f1f5;border-bottom:1px solid #eedccb;padding:12px 28px;display:flex;gap:12px;flex-wrap:wrap;align-items:center}.filters{display:flex;gap:7px;flex-wrap:wrap;flex:1}button,input{font:inherit;border:1px solid #d2b9a6;background:#fffcf8;color:#402517;border-radius:9px;padding:8px 12px;min-height:44px}button{cursor:pointer}.selected{background:#b9431c;color:white;border-color:#b9431c}input{width:250px}main{max-width:1400px;padding:24px 28px 40px;margin:auto;display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:22px}article{background:#fffcf8;border:1px solid #eedccb;border-radius:14px;overflow:hidden;min-width:0}.shot{display:block;aspect-ratio:512/324;background-repeat:no-repeat;background-color:#fff}.caption{padding:16px;display:flex;gap:10px;justify-content:space-between}.caption h2{font-size:17px;margin:2px 0}.caption p{font-size:12px;color:#78675b;margin:0;overflow-wrap:anywhere}.caption small{font-size:12px;color:#78675b}.caption>a{color:#b9431c;white-space:nowrap;font-size:14px;padding-top:18px}a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #b9431c;outline-offset:3px}footer{max-width:1400px;margin:auto;padding:0 28px 35px;color:#78675b}article[hidden]{display:none}@media(max-width:1050px){main{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:650px){main{grid-template-columns:1fr;padding:16px}header{padding:24px 16px}.tools{position:static;padding:12px 16px}input{width:100%}h1{font-size:25px}}
    </style><header><h1>知练同行 · 全页面UI设计</h1><p>59项页面与状态设计；第56项已补完整桌面稿及手机变体。首页与训练计划已确认，其余按清单审核状态查看。点击缩略图查看完整PNG。</p><p>按钮位置见开发详细文档第18节；仅设计交付，应用尚未修改，示例不作为真实用户或算法效果证据。</p></header><section class="tools"><div class="filters">'''+filters+'''</div><input id="search" type="search" placeholder="搜索页面名称" aria-label="搜索页面"><span id="count"></span></section><main>'''+''.join(cards)+'''</main><footer>图片来源与生成提示见《UI设计说明.md》和《页面设计清单.json》。复用稿沿用第四轮布局并更新共同导航。</footer><script>
    const cards=[...document.querySelectorAll('article')],buttons=[...document.querySelectorAll('button[data-group]')],search=document.querySelector('#search');let group='';function filter(){const q=search.value.trim().toLowerCase();let n=0;for(const card of cards){card.hidden=Boolean(group&&card.dataset.group!==group||q&&!card.dataset.search.toLowerCase().includes(q));if(!card.hidden)n++}document.querySelector('#count').textContent=n+' / '+cards.length}buttons.forEach(b=>b.addEventListener('click',()=>{group=b.dataset.group;buttons.forEach(x=>x.classList.toggle('selected',x===b));filter()}));search.addEventListener('input',filter);filter();</script></html>'''
    extra='<section style="max-width:1400px;margin:auto;padding:0 28px 30px"><h2>补充的既有交互状态</h2><p>以下继续复用第四轮状态设计；原截图导航为历史状态，主体交互按本轮统一导航接入。</p><ul>'
    for ref in data.get('state_references',[]):
        extra+='<li><a href="'+quote(ref['file'])+'" target="_blank">'+html.escape(ref['name'])+'</a>：'+html.escape(ref['purpose'])+'</li>'
    extra+='</ul></section>'
    page=page.replace('<footer>',extra+'<footer>',1)
    (ROOT/'UI设计索引.html').write_text(page,encoding='utf-8')
    data['review_sheets']=[dict(file=p.name,bytes=p.stat().st_size) for p in sheets];data['contact_sheet_bytes']=sum(sizes);save(data)
    print('INDEX_READY '+json.dumps({'items':len(cards),'sheets':len(sheets),'review_bytes':sum(sizes)},ensure_ascii=False),flush=True)

def main():
    safe(ROOT);data=json.loads(MANIFEST.read_text(encoding='utf-8'))
    mode=sys.argv[1] if len(sys.argv)>1 else 'all'
    if mode=='index':
        build_index(data);return
    for item in data['items']:
        if item['kind']=='approved':
            path=safe(UI/item['file'])
            with Image.open(path) as im:item.update(width=im.width,height=im.height,bytes=path.stat().st_size)
        elif item['kind']=='reuse' and item['status']!='ready':
            reuse(item);save(data)
    save(data)
    if mode=='reuse':return
    pending=[x for x in data['items'] if x['kind']=='generate' and x['status'] not in ('ready','approved')]
    print('GENERATE_COUNT '+str(len(pending)),flush=True)
    for index,item in enumerate(pending,1):
        print('START '+str(index)+'/'+str(len(pending))+' '+item['file'],flush=True)
        generate(data,item);save(data)
    print('BATCH_FINISHED '+json.dumps({state:sum(x['status']==state for x in data['items']) for state in ('approved','ready','failed','pending')},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
