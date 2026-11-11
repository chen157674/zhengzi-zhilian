"""核验当前版本技术PDF和演示视频，不修改应用数据。"""
from pathlib import Path
import hashlib,json,sys
ROOT=Path(__file__).resolve().parents[1];TASK=Path(__file__).resolve().parent
sys.path.append('C:/Users/chen/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Lib/site-packages')
import av,numpy as np
from PIL import Image,ImageDraw,ImageFont
from pypdf import PdfReader

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 evidence=TASK/'实现核对';report_path=evidence/'交付材料核对.json';previous=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {};start=json.loads((evidence/'源码交付启动核对.json').read_text(encoding='utf-8'))
 for p,h in {**start['source_files'],**start['resource_manifests']}.items():assert sha(ROOT/p)==h,p
 recording=json.loads((evidence/'演示录制核对.json').read_text(encoding='utf-8'));assert recording['source_fingerprint']==start['source_fingerprint'];assert recording['script_errors']==[] and recording['cloud_generation_calls']==0
 pdf_path=TASK/'作品技术文档.pdf';video_path=TASK/'作品演示视频.mp4';pdf=PdfReader(pdf_path)
 assert 1<=len(pdf.pages)<=30 and all(len(p.extract_text())>100 for p in pdf.pages)
 assert start['source_fingerprint'] in '\n'.join(p.extract_text() for p in pdf.pages)
 rows=recording['sections'];assert len(rows)==7;offsets=[];cursor=0
 for r in rows:
  duration=(r['end_ms']-r['start_ms'])/1000;assert duration>=r['voice_seconds'];offsets.append((cursor,cursor+duration));cursor+=duration
 samples=sorted([(a+min(10,(b-a)/3),i,0) for i,(a,b) in enumerate(offsets)]+[(a+(b-a)*.75,i,1) for i,(a,b) in enumerate(offsets)])
 frames=[];count=0;last_time=0
 with av.open(str(video_path)) as c:
  stream=c.streams.video[0];assert (stream.width,stream.height)==(1920,1080);assert 29.9<=float(stream.average_rate)<=30.1;assert stream.codec_context.name=='h264';duration=c.duration/av.time_base;assert 300<=duration<=480 and abs(duration-cursor)<1
  for f in c.decode(video=0):
   count+=1;last_time=float(f.time or 0)
   while len(frames)<len(samples) and last_time>=samples[len(frames)][0]:frames.append(f.to_image().convert('RGB'))
 assert len(frames)==14 and abs(last_time-duration)<1
 energy=[[] for r in rows];audio_frames=0
 with av.open(str(video_path)) as c:
  assert c.streams.audio[0].codec_context.name=='aac'
  for f in c.decode(audio=0):
   audio_frames+=1;stamp=float(f.time or 0)
   for i,(a,b) in enumerate(offsets):
    if a<=stamp<min(a+rows[i]['voice_seconds'],b):
     raw=f.to_ndarray();signal=raw.astype(np.float64)
     if np.issubdtype(raw.dtype,np.integer):signal/=np.iinfo(raw.dtype).max
     energy[i].append(float(np.sqrt(np.mean(signal**2))));break
 rms=[float(np.mean(x)) for x in energy];assert all(x>.005 for x in rms),rms
 width,height=480,270;sheet=Image.new('RGB',(width*2,(height+32)*7),'#eee8df');draw=ImageDraw.Draw(sheet);font=ImageFont.truetype(str(ROOT/'data/fonts/noto-sans-sc/GuanDongSansSC-Regular.ttf'),17)
 for frame,(stamp,i,column) in zip(frames,samples):
  x,y=column*width,i*(height+32);sheet.paste(frame.resize((width,height)),(x,y+32));draw.text((x+8,y+6),f'{stamp:.0f}s '+rows[i]['title'],font=font,fill='#402517')
 contact=evidence/'作品演示视频-画面核对.jpg';sheet.save(contact,quality=76)
 report={'source_fingerprint':start['source_fingerprint'],'source_files_checked':len(start['source_files']),'resource_manifests_checked':len(start['resource_manifests']),'technical_pdf':{'pages':len(pdf.pages),'sha256':sha(pdf_path),'bytes':pdf_path.stat().st_size},'demo_video':{'seconds':duration,'width':1920,'height':1080,'fps':30,'decoded_frames':count,'decoded_audio_frames':audio_frames,'speech_rms_by_section':rms,'sha256':sha(video_path),'bytes':video_path.stat().st_size},'contact_sheet':contact.name,'visual_review':'pending','audio_scope':'逐段音轨完整解码和非静音检查；普通话来自本地SAPI，不冒称人工听审','result':'automated_checks_passed_visual_review_pending'}
 report['contact_sheet_sha256']=sha(contact)
 same_artifacts=(previous.get('source_fingerprint')==report['source_fingerprint'] and previous.get('technical_pdf',{}).get('sha256')==report['technical_pdf']['sha256'] and previous.get('demo_video',{}).get('sha256')==report['demo_video']['sha256'] and previous.get('contact_sheet_sha256')==report['contact_sheet_sha256'])
 if same_artifacts and previous.get('visual_review') not in {None,'pending'}:
  report['visual_review']=previous['visual_review'];report['result']='passed_with_documented_scope'
 build=TASK/'.verification/video-production';profile=ROOT/'.tmp/tests/round5-delivery-video'
 if build.exists():
  assert not build.is_symlink() and build.resolve()==build.absolute(),'制作目录存在链接或边界变化'
  entries=list(build.rglob('*'));assert not any(p.is_symlink() or p.is_junction() for p in entries),'制作目录含链接'
  report['cleanup']={'recording_service_profile_database_removed':not profile.exists(),'retained_build_directory':str(build),'retained_bytes':sum(p.stat().st_size for p in entries if p.is_file()),'reason':previous.get('cleanup',{}).get('reason','制作临时目录尚未清理；核验脚本只读检查，不执行删除。'),'status':'pending_policy_block' if previous.get('cleanup',{}).get('status')=='pending_policy_block' else 'pending_cleanup'}
  report['result']='artifacts_passed_cleanup_pending' if report['visual_review']!='pending' else 'automated_checks_passed_visual_review_and_cleanup_pending'
 else:
  report['cleanup']={'recording_service_profile_database_removed':not profile.exists(),'build_directory_removed':True,'status':'completed' if not profile.exists() else 'pending_profile_cleanup'}
 report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
