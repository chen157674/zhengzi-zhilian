"""按已确认视频结果设计逐张修订纠正提示，不修改应用。"""
from pathlib import Path
import json,subprocess,sys,time
from PIL import Image
ROOT=Path(__file__).resolve().parent
PROJECT=ROOT.parent
MANIFEST=ROOT/'页面设计清单.json'
HELPER=Path(r'E:\07_Projects\90_KnowledgeBase\AI工具维护\ccswitch维护\内置生图兼容入口.py')
data=json.loads(MANIFEST.read_text(encoding='utf-8'))
pending=[x['file'] for x in data['items'] if x.get('revision_status') in ('pending','failed')]
# Prioritize live running, then its responsive version, then remaining states.
pending.sort(key=lambda n:(0 if n=='26_实时分析.png' else 1 if n=='46_实时分析手机.png' else 2,n))
for i,name in enumerate(pending,1):
 data=json.loads(MANIFEST.read_text(encoding='utf-8'))
 item=next(x for x in data['items'] if x['file']==name)
 out=ROOT/'UI图'/name
 if ROOT.is_symlink() or ROOT.is_junction() or out.is_symlink() or out.is_junction():raise RuntimeError('目标边界含链接')
 command=[sys.executable,'-B',str(HELPER),'--project-root',str(PROJECT),'--output',str(out),'--timeout','600']
 if out.exists():command.append('--replace')
 if name in ('26_实时分析.png','46_实时分析手机.png'):command.append('--preview')
 print(f'START {i}/{len(pending)} {name}',flush=True)
 result=subprocess.run(command,input=item['revision_prompt'],text=True,encoding='utf-8',cwd=PROJECT,capture_output=True)
 reports=[json.loads(l[len('RESULT_JSON:'):]) for l in result.stdout.splitlines() if l.startswith('RESULT_JSON:')]
 if result.returncode or len(reports)!=1:
  item.update(revision_status='failed',revision_error=(result.stderr+'\n'+result.stdout)[-2000:])
  print('FAILED '+name+' '+item['revision_error'][:250],flush=True)
 else:
  with Image.open(out) as im:im.verify()
  report=reports[0]
  item.update(status='ready',revision_status='generated_pending_review',revision_method='native_imagegen',width=report['width'],height=report['height'],bytes=report['bytes'],review='已按已确认视频结果设计重绘；待整套联系表检查。')
  item.pop('revision_required',None);item.pop('revision_error',None)
  print('DONE '+name+' '+str(report['width'])+'x'+str(report['height']),flush=True)
 MANIFEST.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
print('CORRECTION_UI_BATCH_FINISHED',flush=True)
