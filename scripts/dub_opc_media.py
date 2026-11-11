"""为已确认的自然录屏合成豆包配音与逐字时间轴；密钥只读环境变量。"""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import socket
import urllib.request
import urllib.error
import uuid
import wave
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / 'OPC专项赛/健身项目视频素材/剪辑工程.json'
ASSETS = ROOT / 'OPC专项赛/健身项目视频素材/配音'
STATE = ASSETS / '配音信息.json'
ENDPOINT = 'https://openspeech.bytedance.com/api/v3/tts/unidirectional'
SPEAKER = 'zh_male_ruyayichen_uranus_bigtts'
CONTEXT = ['请用清晰自然的普通话，平稳地讲解项目功能，避免夸张广告腔。']


def inside(path):
    path = Path(path).resolve()
    path.relative_to(ROOT.resolve())
    return path


def load_state():
    if STATE.is_file():
        return json.loads(STATE.read_text(encoding='utf-8'))
    return {'provider':'doubao-tts-2.0','speaker':SPEAKER,'sample_rate':24000,
            'budget':'free_quota_only','sections':{},'usage_characters':0}


def save_state(state):
    inside(STATE).write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def synthesize(index,quota,regenerate=False):
    manifest = json.loads(inside(MANIFEST).read_text(encoding='utf-8'))
    plan = sum(len(s['narration'])+sum(map(len,CONTEXT)) for s in manifest['sections'])
    if quota < plan+1000 or plan>2000:
        raise ValueError('已核实的免费额度不足，拒绝发送')
    inside(ASSETS).mkdir(exist_ok=True)
    state=load_state();section=manifest['sections'][index];text=section['narration']
    params={'text':text,'speaker':SPEAKER,'audio_params':{'format':'pcm','sample_rate':24000,
        'speech_rate':-10,'enable_subtitle':True},'additions':json.dumps({'context_texts':CONTEXT,'pronunciation_dict':{'tone':['知练同行/(zhi1)(lian4)(tong2)(xing2)']}},ensure_ascii=False)}
    signature=hashlib.sha256(json.dumps(params,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    previous=state['sections'].get(str(index))
    target=inside(ASSETS/f'{index+1:02d}.wav')
    if previous:
        if previous['status']=='completed' and previous['signature']==signature and target.is_file():
            print(json.dumps({'section':index,'status':'cached','seconds':previous['seconds']},ensure_ascii=False),flush=True)
            return
        if regenerate and previous['status']=='completed':
            state.setdefault('retired_requests',[]).append({'section':index,'request_id':previous['request_id'],
                'usage_characters':previous.get('usage_characters',0),'reason':'pronunciation_or_narration_updated'})
        else:
            raise ValueError('该段已有请求记录，未自动重发；请先核对原请求')
    key=os.environ.get('VOLC_TTS_API_KEY','').strip()
    if not key or not key.isascii() or any(ord(c)<=32 for c in key):
        raise ValueError('请在用户环境变量配置 VOLC_TTS_API_KEY')
    request_id=str(uuid.uuid4())
    state['observed_available_characters_before_run']=quota
    state['observed_at']=datetime.now(timezone(timedelta(hours=8))).isoformat()
    state['sections'][str(index)]={'status':'requested','signature':signature,'request_id':request_id,
        'text':text,'requested_characters':len(text),'context_characters':sum(map(len,CONTEXT))}
    save_state(state)
    raw=bytearray();words={};usage=0;logid=None
    request=urllib.request.Request(ENDPOINT,data=json.dumps({'req_params':params},ensure_ascii=False).encode('utf-8'),
        method='POST',headers={'Content-Type':'application/json','X-Api-Key':key,
        'X-Api-Resource-Id':'seed-tts-2.0','X-Api-Request-Id':request_id,
        'X-Control-Require-Usage-Tokens-Return':'*'})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=90) as response:
            if response.geturl()!=ENDPOINT:raise ValueError('API地址发生变化')
            logid=response.headers.get('X-Tt-Logid')
            for line in response:
                if not line.strip():continue
                if len(line)>4*1024*1024:raise ValueError('响应分块异常')
                value=json.loads(line)
                code=value.get('code',0)
                if code not in (0,20000000):
                    state['sections'][str(index)].update(status='provider_rejected',provider_code=code)
                    save_state(state)
                    print(json.dumps({'section':index,'status':'provider_rejected','provider_code':code}),flush=True)
                    raise SystemExit(1)
                if value.get('data'):
                    raw.extend(base64.b64decode(value['data'],validate=True))
                    if len(raw)>32*1024*1024:raise ValueError('音频大小超限')
                for word in (value.get('sentence') or {}).get('words',[]):
                    if isinstance(word.get('word'),str) and type(word.get('startTime')) in (int,float) and type(word.get('endTime')) in (int,float):
                        start,end=float(word['startTime']),float(word['endTime'])
                        if 0<=start<end:words[(start,end,word['word'])]={'start':start,'end':end,'text':word['word']}
                measured=(value.get('usage') or {}).get('text_words')
                if type(measured) is int:usage=max(usage,measured)
        if not raw or len(raw)%2:raise ValueError('未返回完整PCM音频')
        seconds=len(raw)/48000
        timeline=sorted(words.values(),key=lambda w:(w['start'],w['end']))
        if not timeline or max(w['end'] for w in timeline)>seconds+.5:raise ValueError('字级时间轴缺失或越界')
        with wave.open(str(target),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(24000);audio.writeframes(raw)
        item=state['sections'][str(index)]
        item.update(status='completed',file=target.name,seconds=round(seconds,4),words=timeline,
            usage_characters=usage,audio_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),logid=logid)
        state['usage_characters']=sum(v.get('usage_characters',0) for v in state['sections'].values())+sum(v.get('usage_characters',0) for v in state.get('retired_requests',[]))
        save_state(state)
        print(json.dumps({'section':index,'status':'completed','seconds':round(seconds,3),
            'words':len(timeline),'last_word_end':max(w['end'] for w in timeline),'usage_characters':usage},ensure_ascii=False),flush=True)
    except urllib.error.HTTPError as error:
        state['sections'][str(index)].update(status='http_rejected',http_status=error.code);save_state(state)
        print(json.dumps({'section':index,'status':'http_rejected','http_status':error.code}),flush=True)
        error.close();raise SystemExit(1) from None
    except (urllib.error.URLError,TimeoutError,socket.timeout,ConnectionError,OSError,ValueError) as error:
        state['sections'][str(index)].update(status='uncertain',error_type=type(error).__name__);save_state(state)
        print(json.dumps({'section':index,'status':'uncertain','error_type':type(error).__name__}),flush=True)
        raise SystemExit(1) from None
    finally:
        key=None


def ass_time(seconds):
    cs=max(0,round(seconds*100))
    return f'{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}'


def render_video():
    manifest=json.loads(inside(MANIFEST).read_text(encoding='utf-8'));state=load_state()
    duration=manifest['duration'];build=inside(ROOT/'.tmp/opc-completion')
    preview=inside(ROOT/manifest['output']);final=inside(ROOT/'OPC专项赛/知练同行_作品评审视频.mp4')
    pending=inside(build/'footage-dubbed.mp4')
    ffmpeg=Path(r'E:\07_Projects\07_MonetizationTests\02_KnowledgeVideos\open-source\huashu-art-motion\.venv\Scripts\ffmpeg.exe')
    anchors=[[.5,11.5],[.5,9,22,31,40],[.5,11.5,24,39],[.5,12.5,30],[.5,14,29,36],[.5,12],[1]]
    groups=[];captions=[];alignments=[]
    for index,section in enumerate(manifest['sections']):
        item=state['sections'].get(str(index))
        if not item or item['status']!='completed' or item['text']!=section['narration']:
            raise ValueError('配音尚未完成或解说词已变化')
        path=inside(ASSETS/item['file'])
        if hashlib.sha256(path.read_bytes()).hexdigest()!=item['audio_sha256']:
            raise ValueError('配音文件摘要不符')
        sentences=[];part=[]
        for word in item['words']:
            part.append(word)
            if re.search(r'[。！？]$',word['text']):sentences.append(part);part=[]
        if part:sentences.append(part)
        if len(sentences)!=len(anchors[index]):raise ValueError('配音语句与画面锚点不匹配')
        if ''.join(w['text'] for w in item['words'])!=section['narration']:
            raise ValueError('字幕文本没有完整对应解说词')
        previous_end=section['start']
        for number,words in enumerate(sentences):
            source_start=max(0,words[0]['start']-.04)
            source_end=min(item['seconds'],words[-1]['end']+.12)
            if number+1<len(sentences):source_end=min(source_end,sentences[number+1][0]['start']-.02)
            target_start=max(section['start']+anchors[index][number],previous_end+.35)
            available=section['start']+section['duration']-.25-target_start
            tempo=max(.85,(source_end-source_start)/available)
            if not .8<=tempo<=1.25:raise ValueError('语音无法自然放入已确认画面')
            target_end=target_start+(source_end-source_start)/tempo
            previous_end=target_end
            group={'input':index+1,'source_start':source_start,'source_end':source_end,'target_start':target_start,
                   'target_end':target_end,'tempo':tempo,'words':words}
            groups.append(group)
            alignments.append({k:group[k] for k in ('input','source_start','source_end','target_start','target_end','tempo')})
            current=[];length=0
            for word in words:
                if current and (length+len(word['text'])>26 or word['start']-current[-1]['end']>.6):
                    captions.append((target_start+(current[0]['start']-source_start)/tempo,
                        min(target_end,target_start+(current[-1]['end']-source_start)/tempo+.1),''.join(v['text'] for v in current)))
                    current=[];length=0
                current.append(word);length+=len(word['text'])
                if re.search(r'[，。！？；]$',word['text']) and length>=12:
                    captions.append((target_start+(current[0]['start']-source_start)/tempo,
                        min(target_end,target_start+(current[-1]['end']-source_start)/tempo+.1),''.join(v['text'] for v in current)))
                    current=[];length=0
            if current:captions.append((target_start+(current[0]['start']-source_start)/tempo,
                min(target_end,target_start+(current[-1]['end']-source_start)/tempo+.1),''.join(v['text'] for v in current)))
    for start,end,content in captions:
        if not 0<=start<end<=duration or len(content)>40:raise ValueError('字幕范围或长度不正确')
    header="""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Narration,GuanDongSansSC,32,&H00FFFFFF,&H00FFFFFF,&H00202020,&H00000000,0,0,0,0,100,100,0,0,1,1.8,0,2,70,70,30,1
Style: Speed,GuanDongSansSC,20,&H00FFFFFF,&H00FFFFFF,&H00333333,&H00000000,0,0,0,0,100,100,0,0,1,1.2,0,9,0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events=[f'Dialogue: 0,{ass_time(start)},{ass_time(end)},Narration,,0,0,0,,{content}' for start,end,content in captions]
    at=0
    for section in manifest['sections']:
        for clip in section['clips']:
            span=(clip['out']-clip['in'])/clip['speed']
            if clip['speed']>=10:
                events.append(f'Dialogue: 1,{ass_time(at)},{ass_time(at+span)},Speed,,0,0,0,,{{\\pos(1880,110)}}分析等待已加速')
            at+=span
    subtitle=inside(ASSETS/'字幕.ass');subtitle.write_text(header+'\n'.join(events)+'\n',encoding='utf-8-sig')
    # 按采样点混音，避免多路延迟滤镜在当前 FFmpeg 中产生异常时间戳。
    sample_rate=48000
    mixed=np.zeros(round(duration*sample_rate),dtype=np.float32)
    normalized={}
    for input_index in range(1,len(manifest['sections'])+1):
        path=inside(ASSETS/state['sections'][str(input_index-1)]['file'])
        result=subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-i',str(path),
            '-af','loudnorm=I=-16:TP=-1.5:LRA=7','-ac','1','-ar',str(sample_rate),
            '-f','f32le','-'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        normalized[input_index]=np.frombuffer(result.stdout,dtype='<f4')
    for g in groups:
        source=normalized[g['input']]
        piece=source[round(g['source_start']*sample_rate):round(g['source_end']*sample_rate)]
        result=subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error',
            '-f','f32le','-ac','1','-ar',str(sample_rate),'-i','pipe:0',
            '-af',f'atempo={g["tempo"]:.6f}', '-f','f32le','-'],
            input=piece.tobytes(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        speech=np.frombuffer(result.stdout,dtype='<f4').copy()
        if not len(speech) or np.max(np.abs(speech))<.001:
            raise ValueError('配音片段为空或静音')
        fade_in=min(round(.01*sample_rate),len(speech))
        fade_out=min(round(.05*sample_rate),len(speech))
        speech[:fade_in]*=np.linspace(0,1,fade_in)
        speech[-fade_out:]*=np.linspace(1,0,fade_out)
        start=round(g['target_start']*sample_rate);end=min(len(mixed),start+len(speech))
        mixed[start:end]+=speech[:end-start]
    if np.max(np.abs(mixed))>=1:
        raise ValueError('配音混音发生削波')
    pcm=(np.clip(mixed,-1,1)*32767).astype('<i2').tobytes()
    command=[str(ffmpeg),'-hide_banner','-loglevel','error','-y','-threads','2','-i',str(preview),
        '-f','s16le','-ar',str(sample_rate),'-ac','1','-i','pipe:0',
        '-vf','subtitles=../../OPC专项赛/健身项目视频素材/配音/字幕.ass:fontsdir=../../data/fonts/noto-sans-sc,format=yuv420p',
        '-map','0:v:0','-map','1:a:0',
        '-t',str(duration),'-c:v','h264_nvenc','-preset','p4','-rc','vbr','-cq','20','-b:v','0',
        '-pix_fmt','yuv420p','-c:a','aac','-b:a','160k','-ar','48000','-ac','2','-movflags','+faststart',str(pending)]
    result=subprocess.run(command,cwd=build,input=pcm,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[-2000:])
    subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-threads','2','-i',str(pending),'-map','0:v:0','-map','0:a:0','-f','null','-'],check=True)
    decoded=subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-i',str(pending),
        '-vn','-ac','1','-ar',str(sample_rate),'-f','f32le','-'],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    audio=np.frombuffer(decoded.stdout,dtype='<f4')
    if abs(len(audio)/sample_rate-duration)>.05:
        raise ValueError('输出音轨时长不符')
    checks=[]
    for g in groups:
        segment=audio[round(g['target_start']*sample_rate):round(g['target_end']*sample_rate)]
        rms=float(np.sqrt(np.mean(segment**2)))
        if rms<.005:
            raise ValueError('输出语音片段缺失')
        checks.append({'start':round(g['target_start'],3),'end':round(g['target_end'],3),
            'rms_dbfs':round(float(20*np.log10(rms)),2)})
    inside(pending);inside(final);pending.replace(final)
    state['alignment']=alignments;state['caption_count']=len(captions)
    state['final_output']=str(final.relative_to(ROOT));state['full_decode_passed']=True
    state['audio_spot_checks']=checks;state['audio_seconds']=round(len(audio)/sample_rate,4);save_state(state)
    manifest['final_output']=str(final.relative_to(ROOT));manifest['audio_status']='completed'
    manifest['voiceover_plan'].update(generation_performed=True,usage_characters=state['usage_characters'])
    manifest['verification']['review_status']='visual_approved'
    manifest['final_verification']={'seconds':duration,'width':1920,'height':1080,'fps':30,'bytes':final.stat().st_size,
        'voiceover':True,'captions':True,'caption_count':len(captions),'added_background':False,'split_screen':False,
        'full_decode_passed':True,'audio_spot_checks':checks,'audio_seconds':round(len(audio)/sample_rate,4),
        'sha256':hashlib.sha256(final.read_bytes()).hexdigest()}
    inside(MANIFEST).write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(final),'seconds':duration,'caption_count':len(captions),'speech_segments':len(groups),'usage_characters':state['usage_characters'],'full_decode_passed':True},ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--only',type=int)
    parser.add_argument('--regenerate',action='store_true')
    parser.add_argument('--free-quota-chars',type=int)
    parser.add_argument('--render',action='store_true')
    args=parser.parse_args()
    if args.render:
        render_video();return
    if args.free_quota_chars is None:raise ValueError('合成前必须明确已核实的免费额度')
    inside(ASSETS)
    os.environ['TEMP']=str(inside(ROOT/'.tmp/opc-completion'));os.environ['TMP']=os.environ['TEMP']
    manifest=json.loads(MANIFEST.read_text(encoding='utf-8'))
    for index in ([args.only] if args.only is not None else range(len(manifest['sections']))):
        synthesize(index,args.free_quota_chars,args.regenerate)


if __name__=='__main__':
    try:main()
    except (ValueError,KeyError,IndexError) as error:
        print(json.dumps({'status':'configuration_error','error_type':type(error).__name__}),flush=True)
        raise SystemExit(1) from None
