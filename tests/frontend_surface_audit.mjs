// 页面覆盖审查：独立账号和数据库，保留正常有数据/无数据场景，不请求相机或云端AI。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('frontend-surface-audit'),output=path.join(root,'第五轮优化/实现核对');
const rows=[],errors=[],images=[];
const focused=process.argv.includes('--focused'),prefix=focused?'前端重点复测':'前端全页面';
try {
  const ids=JSON.parse(fx.seed(`import os,json
from sqlalchemy import select
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
from scripts.seed_review_account import seed,USERNAME,make_plan,ZONE
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
db=app.extensions['database'];initialize_admin(db,'Audit_Admin','audit-admin-password')
seed(os.environ['FITNESS_DATABASE_PATH'],True,'audit-review-password')
client=app.test_client();token=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
assert client.post('/api/v1/auth/login',json={'username':USERNAME,'password':'audit-review-password'},headers={'X-CSRFToken':token}).status_code==200
with client.session_transaction() as state:auth=state['_user_id']
with db.transaction(False) as c:
 uid=c.scalar(select(s.users.c.id).where(s.users.c.username_key==USERNAME))
 workouts=[dict(x) for x in c.execute(select(s.training_workouts).where(s.training_workouts.c.user_id==uid)).mappings()]
 records=c.scalars(select(s.analysis_sessions.c.id).where(s.analysis_sessions.c.user_id==uid).order_by(s.analysis_sessions.c.started_at_ms.desc())).all()
plans=app.extensions['training_plans'];current=plans.current(uid,auth)['plan']
draft=plans.create(uid,auth,make_plan(__import__('datetime').date.fromisoformat(current['start_date'])),s.new_id())
today=app.extensions['training_progress'].today(uid,auth)
if not today.get('session') or today['session']['scheduled_date']!=plans.today().isoformat():
 import copy
 latest=plans.get(uid,auth,current['id']);sessions=copy.deepcopy(latest['revision']['sessions'])
 day=plans.today().isoformat();entry=copy.deepcopy(next(x for x in sessions if x['day_kind']=='training'))
 entry.update(client_id='audit-today-'+day,date=day)
 for index,item in enumerate(entry['items']):item['client_id']='audit-today-item-'+str(index)
 sessions=[x for x in sessions if x['date']!=day]+[entry]
 constraints=copy.deepcopy(latest['revision']['constraints']);constraints['training_weekdays']=sorted(set(constraints['training_weekdays']+[plans.today().isoweekday()]))
 plans.edit(uid,auth,current['id'],{'expected_version':latest['state_version'],'effective_date':day,'edited_revision':{'constraints':constraints,'sessions':sessions}},s.new_id())
 today=app.extensions['training_progress'].today(uid,auth)
run=app.extensions['training_workouts'].create(uid,auth,{'plan_session_id':today['session']['id'],'session_expected_version':0},s.new_id())
run=app.extensions['training_workouts'].command(uid,auth,run['id'],{'expected_version':0,'type':'start'},s.new_id())
completed=[x for x in workouts if x['status']=='completed']
voided=app.extensions['training_workouts'].command(uid,auth,completed[-1]['id'],{'expected_version':completed[-1]['state_version'],'type':'void','confirmed':True},s.new_id())
task=None
for record in records:
 options=app.extensions['retests'].options(uid,record,auth)['options']
 if options:
  task=app.extensions['coach_retests'].save(uid,auth,s.new_id(),{'origin':options[0]['origin'],'confirmed':True});break
source=next(x['id'] for x in app.extensions['knowledge_store'].by_id.values() if x['status']=='published')
print(json.dumps({'plan':current['id'],'draft':draft['id'],'running':run['id'],'completed':completed[0]['id'],'partial':next(x['id'] for x in workouts if x['status']=='ended_early'),'voided':voided['id'],'record':records[0],'task':task['task_id'] if task else None,'source':source}))
for hook in app.extensions.get('stop_hooks',[]):hook()
db.dispose()`));
  fx.start();
  const context=await fx.browser('Edge',{width:1440,height:1000});
  await fx.apiLogin(context,'review_judge','audit-review-password');
  const page=await context.newPage();page.on('pageerror',error=>errors.push({url:page.url(),message:error.message}));
  const cloud=[];
  page.on('request',request=>{if(request.method()==='POST'&&/\/(ai\/conversations|coach\/tasks|review-jobs|plan-jobs|jobs)$/.test(new URL(request.url()).pathname))cloud.push(request.url());});
  const routes=[
    ['首页','/'],['实时分析','/realtime'],['视频分析','/video'],['内置视频准备','/video?demo=1&demo_id=user-1694'],['视频已有结果','/video/examples/user-1694'],
    ['训练计划','/training/plan'],['新建计划','/training/plans/new'],['编辑计划','/training/plans/'+ids.plan+'/edit'],['启用计划','/training/plans/'+ids.draft+'/activate'],
    ['训练进行','/training/workouts/'+ids.running],['训练完成','/training/workouts/'+ids.completed+'/summary'],['训练部分完成','/training/workouts/'+ids.partial+'/summary'],['训练作废','/training/workouts/'+ids.voided+'/summary'],
    ['训练记录','/records?view=list'],['记录详情','/records/'+ids.record],['记录旧概览','/records?view=overview'],['成长看板','/training/review'],
    ['动作指南','/coach/knowledge'],['动作目录','/coach/knowledge?view=actions'],['动作详情','/actions/bodyweight_squat'],['练习准备','/actions/bodyweight_squat/practice'],['来源详情','/coach/knowledge/'+ids.source],['参考文献','/coach/references'],
    ['饮食建议','/coach/nutrition'],['餐食记忆','/coach/meals'],['AI教练','/coach'],['纠正任务','/coach/tasks'],
    ...(ids.task?[['纠正对比','/coach/tasks/'+ids.task+'/compare']]:[]),
    ['个人中心','/account'],['健身档案','/account/training-profile'],['账号安全','/account/password'],['登录管理','/account/sessions'],['AI设置','/account/ai'],['设置','/settings'],
    ['权限不足','/admin/users',403],['页面不存在','/not-a-published-page',404],
  ];
  async function inspect(label,route,status=200){
    const response=await page.goto(fx.origin+route,{waitUntil:'networkidle'});
    assert.equal(response.status(),status,label+'响应错误');
    await page.locator('main h1:visible').first().waitFor();
    await page.evaluate(async()=>{await Promise.all(Array.from(document.images).filter(img=>{const r=img.getBoundingClientRect();return r.width&&r.height&&r.top<innerHeight&&r.bottom>0;}).map(img=>img.decode().catch(()=>{})));});
    const result=await page.evaluate(()=>{
      const main=document.querySelector('main'),box=main.getBoundingClientRect(),heading=Array.from(main.querySelectorAll('h1')).find(h=>h.getClientRects().length);
      const clippedControls=Array.from(main.querySelectorAll('table,input:not([type=hidden]),select,textarea,button')).filter(el=>{
        const r=el.getBoundingClientRect();if(!r.width||!r.height||getComputedStyle(el).visibility==='hidden')return false;
        for(let parent=el.parentElement;parent&&parent!==main;parent=parent.parentElement){
          if(/auto|scroll/.test(getComputedStyle(parent).overflowX)&&parent.scrollWidth>parent.clientWidth+1)return false;
        }
        const parent=el.parentElement.getBoundingClientRect(),right=el.tagName==='TABLE'?Math.min(innerWidth,parent.right):innerWidth;
        return r.left<-1||r.right>right+1;
      }).map(el=>({tag:el.tagName,id:el.id,text:(el.textContent||el.getAttribute('aria-label')||'').slice(0,32),right:el.getBoundingClientRect().right}));
      return {page:document.body.dataset.page,title:heading?.textContent.trim(),width:innerWidth,viewportHeight:innerHeight,scrollWidth:document.documentElement.scrollWidth,
        mainWidth:box.width,headingSize:getComputedStyle(heading).fontSize,
        openDialogs:document.querySelectorAll('dialog[open]').length,
        contentPanels:Array.from(main.querySelectorAll('.knowledge-answer,.settings-card,.workspace-card')).slice(0,8).map(el=>({id:el.id,padding:getComputedStyle(el).padding,border:getComputedStyle(el).borderRadius})),
        alerts:Array.from(main.querySelectorAll('[role=alert]:not([hidden])')).filter(n=>n.textContent.trim()).map(n=>n.textContent.trim()),
        clippedControls,brokenImages:Array.from(main.querySelectorAll('img')).filter(img=>{const r=img.getBoundingClientRect();return r.width&&r.height&&r.top<innerHeight&&r.bottom>0&&img.complete&&!img.naturalWidth;}).map(img=>img.getAttribute('src'))};
    });
    assert(result.scrollWidth<=result.width,label+'横向溢出 '+JSON.stringify(result));
    assert.deepEqual(result.brokenImages,[],label+'首屏图片不可用');
    assert.deepEqual(result.clippedControls,[],label+'控件或表格被裁切');
    rows.push({label,route,status,...result});
    if(rows.length%12===0)console.log('已核对 '+rows.length+' 个页面状态，当前：'+label);
    const image=path.join(fx.temporary,images.length+'.png');
    await page.screenshot({path:image,animations:'disabled'});images.push({label:label+(result.width<768?' · 手机':''),path:image});
    if(['训练完成','训练部分完成','训练进行','来源详情','纠正任务','登录管理','AI设置'].includes(label)){
      await page.screenshot({path:path.join(output,'前端复核-'+label+'-'+result.width+'.png'),fullPage:true,animations:'disabled'});
    }
  }
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:width<768?844:1000});
    for(const route of focused?routes.filter(([label])=>['训练进行','训练完成','训练部分完成','训练作废','来源详情','纠正任务','纠正对比','参考文献'].includes(label)):routes)await inspect(...route);
  }
  for(const width of [320,768,1024]){
    await page.setViewportSize({width,height:900});
    for(const route of routes.filter(([label])=>['训练进行','训练作废','训练完成','训练部分完成','来源详情','纠正任务','纠正对比','参考文献',...(focused?[]:['AI设置'])].includes(label)))await inspect(...route);
  }
  await page.setViewportSize({width:390,height:844});
  await page.goto(fx.origin+'/training/workouts/'+ids.completed+'/summary',{waitUntil:'networkidle'});
  await page.getByRole('button',{name:'查看本次记录',exact:true}).click();
  assert.equal(await page.locator('#workout-summary-details').getAttribute('open'),'');
  await page.getByText('备注',{exact:true}).click();
  await page.getByRole('textbox',{name:'本次训练备注'}).fill('复核保存：下次保持相同节奏。');
  await page.getByRole('button',{name:'保存备注',exact:true}).click();
  await page.getByText('备注已保存',{exact:true}).waitFor();
  await page.waitForFunction(()=>document.querySelector('#workout-page').getAttribute('aria-busy')==='false'&&!document.querySelector('#workout-note-details button').disabled);
  await page.reload({waitUntil:'networkidle'});
  await page.getByText('备注',{exact:true}).click();
  assert.equal(await page.getByRole('textbox',{name:'本次训练备注'}).inputValue(),'复核保存：下次保持相同节奏。');
  await page.getByText('更多操作',{exact:true}).click();
  await page.getByRole('button',{name:'重新打开执行',exact:true}).click();
  await page.locator('#confirm-dialog').waitFor();
  await page.locator('[data-dialog-cancel]').click();
  assert(await page.locator('#workout-summary').isVisible());
  if(!focused){
  await fx.apiLogin(context,'Audit_Admin','audit-admin-password');
  for(const width of [1440,390]){
    await page.setViewportSize({width,height:width<768?844:1000});
    for(const route of [['账号管理','/admin/users'],['未开放算法入口','/admin/experiments',404],['管理员AI设置','/account/ai']])await inspect(...route);
  }
  // 无训练数据的账号核对真实空状态；不伪造空接口。
  for(const route of [['计划空状态','/training/plan'],['记录空状态','/records?view=list'],['成长空状态','/training/review'],['餐食空状态','/coach/meals'],['纠正任务空状态','/coach/tasks']])await inspect(...route);
  const anonymous=await fx.browser('Chrome',{width:390,height:844});
  const publicPage=await anonymous.newPage();
  for(const route of ['/login','/register']){
    await publicPage.goto(fx.origin+route,{waitUntil:'networkidle'});
    assert(await publicPage.locator('main h2:visible').first().isVisible());
    assert(await publicPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    rows.push({label:route==='/login'?'登录':'注册',route,width:390,status:200});
  }
  }
  assert.deepEqual(cloud,[],'审查不得发起AI或视频分析');
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(output,focused?'前端重点复测.json':'前端全页面复核.json'),JSON.stringify({checked_at:new Date().toISOString(),scope:focused?'changed-pages':'all-served-pages',rows,errors,provider_requests:cloud.length,interactions:['展开分组明细','备注保存后刷新仍保留','重新打开取消保留当前状态']},null,2)+'\n');
  const contactCode=`import sys,json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
data=json.load(sys.stdin);out=Path(data['out'])
font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',17)
for batch in range((len(data['images'])+11)//12):
 canvas=Image.new('RGB',(1600,1000),'#eee8df');draw=ImageDraw.Draw(canvas)
 for i,item in enumerate(data['images'][batch*12:(batch+1)*12]):
  x=(i%4)*400;y=(i//4)*333;draw.text((x+10,y+4),item['label'],font=font,fill='#3e2d24')
  im=Image.open(item['path']).convert('RGB');im.thumbnail((380,292));canvas.paste(im,(x+10,y+32))
 canvas.save(out/(data['prefix']+'-联系表-'+str(batch+1)+'.jpg'),quality=76)
`;
  fx.python(['-c',contactCode],JSON.stringify({out:output,images,prefix}));
  console.log(JSON.stringify({page_states:rows.length,script_errors:errors.length,provider_requests:cloud.length}));
} finally {
  await fx.close();
}
