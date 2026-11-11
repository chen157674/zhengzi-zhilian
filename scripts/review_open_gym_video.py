"""按HTTP字节范围查看已核实许可的长视频；内存缓存，不先下载整段大文件。"""
import io,json,sys,urllib.request,urllib.parse,re,html
from collections import OrderedDict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()


class RangeReader(io.RawIOBase):
    def __init__(self,url):
        self.url=url;self.position=0;self.block_size=1024*1024;self.blocks=OrderedDict();self.downloaded=0;self.failed=None
        request=urllib.request.Request(url,headers={'User-Agent':'FitnessResearch/1.0','Range':'bytes=0-0'})
        with urllib.request.urlopen(request,timeout=40) as r:
            if r.status!=206:raise ValueError('源站不支持范围读取，停止而不下载整段')
            self.length=int(r.headers['Content-Range'].split('/')[-1]);self.etag=r.headers.get('ETag');r.read()
    def readable(self):return True
    def seekable(self):return True
    def tell(self):return self.position
    def seek(self,offset,whence=0):
        self.position=max(0,(0 if whence==0 else self.position if whence==1 else self.length)+offset);return self.position
    def read(self,size=-1):
        if self.failed:raise self.failed
        if size<0:size=self.length-self.position
        size=min(size,self.length-self.position);parts=[]
        while size>0:
            index=self.position//self.block_size
            if index not in self.blocks:
                start=index*self.block_size;end=min(start+self.block_size,self.length)-1
                request=urllib.request.Request(self.url,headers={'User-Agent':'FitnessResearch/1.0','Range':f'bytes={start}-{end}'})
                try:
                    with urllib.request.urlopen(request,timeout=40) as r:
                        if r.status!=206 or not r.headers.get('Content-Range','').startswith(f'bytes {start}-'):raise ValueError('范围返回不符')
                        data=r.read(self.block_size+1)
                except Exception as error:
                    self.failed=error;raise
                if len(data)!=end-start+1:raise ValueError('范围长度不符')
                self.blocks[index]=data;self.downloaded+=len(data)
                while len(self.blocks)>4:self.blocks.popitem(last=False)
            data=self.blocks[index];self.blocks.move_to_end(index);offset=self.position%self.block_size
            part=data[offset:offset+size];parts.append(part);size-=len(part);self.position+=len(part)
        return b''.join(parts)


def source():
    name='47회(등운동)팀GO4IT 다니엘, 박소희by헬스티비.webm'
    url='https://commons.wikimedia.org/wiki/File:'+urllib.parse.quote(name.replace(' ','_'))
    request=urllib.request.Request(url,headers={'User-Agent':'FitnessResearch/1.0'})
    page=urllib.request.urlopen(request,timeout=30).read().decode()
    media=list(dict.fromkeys(html.unescape(v) for v in re.findall(r'https://upload\.wikimedia\.org[^\s\"<>]+webm',page)))
    return {'id':'commons-go4it-back','source_url':url,'media_url':next(v for v in media if '.480p.vp9.webm' in v),
            'author':'헬스티비','license':'CC BY 3.0','license_url':'https://creativecommons.org/licenses/by/3.0/',
            'license_review':'Commons reviewer Explicit confirmed original license on 2017-10-26',
            'use_scope':'Local development; no endorsement; credit author, source and license; identify extracted excerpts'}


def run():
    import av
    from PIL import Image,ImageDraw
    sources=json.loads((ROOT/'data/datasets/development-movements/sources.json').read_text(encoding='utf-8'))['sources']
    info=next((r for r in sources if r['id']=='commons-go4it-back'),None)
    if info and info.get('path'):
        reader=ROOT/info['path'];remote=None
    else:
        info=source();reader=remote=RangeReader(info['media_url'])
    out=ROOT/'data/verification/step-22'
    times=list(range(180,3781,180));sheet=Image.new('RGB',(1280,((len(times)+3)//4)*200),'white');draw=ImageDraw.Draw(sheet)
    with av.open(str(reader) if isinstance(reader,Path) else reader) as video:
        for i,sec in enumerate(times):
            video.seek(sec*1000000)
            frame=next(f for f in video.decode(video=0) if f.time>=sec)
            image=frame.to_image();image.thumbnail((320,180));x=i%4*320;y=i//4*200
            sheet.paste(image,(x,y+20));draw.text((x+5,y+3),f'{sec}s',fill='black')
            print(json.dumps({'reviewed_second':sec,'downloaded_bytes':remote.downloaded if remote else 0}),flush=True)
    sheet.save(out/'go4it-source-review.jpg')
    info.update(source_bytes=remote.length if remote else reader.stat().st_size,source_etag=remote.etag if remote else None,
                reviewed_seconds=times,downloaded_bytes=remote.downloaded if remote else 0)
    (out/'go4it-source.json').write_text(json.dumps(info,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':run()
