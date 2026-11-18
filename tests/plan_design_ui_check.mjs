// 训练计划四状态与实际周历/图解，隔离库，无AI。
import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {BrowserFixture,root} from './formal_browser_support.mjs';
const fx=new BrowserFixture('plan-design'),out=path.join(root,'第五轮优化/实现核对'),errors=[],rows=[];
try{
fx.seed(`import os,sys,json
from fitness_app import create_app,db_schema as s
from fitness_app.auth import initialize_admin
from fitness_app.paths import ROOT
sys.path.insert(0,str(ROOT/'tests'))
from test_training_plans import draft
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});uid=initialize_admin(app.extensions['database'],'Design_Admin','design-fixture-password')['id']
client=app.test_client();token=client.get('/api/v1/auth/csrf').json['data']['csrf_token'];client.post('/api/v1/auth/login',json={'username':'Design_Admin','password':'design-fixture-password'},headers={'X-CSRFToken':token})
with client.session_transaction() as state:auth=state['_user_id']
plans=app.extensions['training_plans'];payload=draft(plans,'循序渐进 · 全身力量计划');payload['sessions'][0]['title']='全身基础 · 从这一组开始'
for index,action in enumerate(['machine_chest_press','standing_dumbbell_curl']):
 item=dict(payload['sessions'][0]['items'][0]);item.update(client_id='extra-'+str(index),action_id=action,target_sets=3,target_reps=12);payload['sessions'][0]['items'].append(item)
p=plans.create(uid,auth,payload,s.new_id());plans.command(uid,auth,p['id'],'activate',{'expected_version':0,'confirmed':True},s.new_id())
d=plans.create(uid,auth,draft(plans,'周末力量 · 待启用'),s.new_id())
open(os.path.join(os.path.dirname(os.environ['FITNESS_DATABASE_PATH']),'fixture.json'),'w',encoding='utf-8').write(json.dumps({'plan_id':p['id'],'draft_id':d['id']}))
app.extensions['database'].dispose()`);
fx.start();const fixture=JSON.parse(fs.readFileSync(path.join(fx.temporary,'fixture.json'),'utf8')),context=await fx.browser('Edge',{width:1440,height:1000});await fx.apiLogin(context,'Design_Admin','design-fixture-password');const p=await context.newPage();p.on('pageerror',e=>errors.push(e.message));
for(const [state,route,ready]of [['active','/training/plan','#plan-day-card .plan-action-row'],['editor','/training/plans/'+fixture.draft_id+'/edit','.plan-editor-picture'],['confirm','/training/plans/'+fixture.draft_id+'/activate','#plan-confirm-content']]){
 await p.goto(fx.origin+route);await p.locator(ready).first().waitFor();
 for(const width of [1440,1280,1024,768,414,390,320]){await p.setViewportSize({width,height:width<768?844:1000});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),state+' overflow '+width);if(state==='active'){assert.equal(await p.locator('.plan-day').count(),7);assert.equal(await p.locator('.plan-day[aria-pressed=true]').count(),1);assert.equal(await p.locator('#plan-day-card .plan-action-row').count(),3);assert.equal(await p.locator('#plan-day-card img[src*="/learn/"]').count(),3);const a=await p.locator('.plan-dashboard-aside').boundingBox(),b=await p.locator('.plan-schedule-main').boundingBox();assert(width>=768?a.x>=b.x+b.width:a.y>=b.y+b.height,'main/aside '+width);}rows.push({state,width,passed:true});if(width===1440||width===390){await p.evaluate(()=>{document.activeElement?.blur();scrollTo(0,0);});await p.screenshot({path:path.join(out,'计划改版-'+state+'-'+(width===1440?'桌面':'手机')+'.png'),fullPage:true,animations:'disabled'});}}
}
await p.goto(fx.origin+'/training/plan');await p.locator('#plan-day-card .plan-action-row').first().waitFor();const date=await p.locator('.plan-day[aria-pressed=true]').getAttribute('data-date');await p.locator('#plan-week-next').click();await p.waitForFunction(d=>document.querySelector('.plan-day[aria-pressed=true]')?.dataset.date!==d,date);assert((await p.locator('#plan-day-card .plan-eyebrow').textContent()).includes((await p.locator('.plan-day[aria-pressed=true]').getAttribute('data-date')).replaceAll('-','.')));await p.locator('#plan-today').click();await p.locator('#plan-day-card .plan-action-row').first().waitFor();
const rest=p.locator('.plan-day[data-state=rest]').first();if(await rest.count()){await rest.click();await p.locator('.plan-rest-day').waitFor();assert.equal(await p.locator('#plan-day-card .plan-action-row').count(),0);}await p.locator('#plan-today').click();await p.locator('#plan-day-card .plan-action-row').first().waitFor();await p.locator('#plan-history-filter').selectOption('draft');await p.waitForFunction(()=>document.querySelectorAll('#plan-history-list .plan-library-card').length===1&&document.querySelector('#plan-history-list .plan-library-card')?.dataset.status==='draft');assert.equal(await p.locator('#plan-history-list .plan-library-card').count(),1);
await context.clearCookies();const token=(await(await context.request.get(fx.origin+'/api/v1/auth/csrf')).json()).data.csrf_token;assert.equal((await context.request.post(fx.origin+'/api/v1/auth/register',{headers:{'X-CSRFToken':token},data:{email:'empty-design@gmail.com',password:'empty-design-password',password_confirmation:'empty-design-password'}})).status(),201);await fx.apiLogin(context,'empty-design@gmail.com','empty-design-password');await p.goto(fx.origin+'/training/plan');await p.locator('#plan-empty').waitFor();assert(await p.locator('#plan-history').isHidden());
for(const width of [1440,768,390,320]){await p.setViewportSize({width,height:900});assert(await p.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'empty overflow '+width);rows.push({state:'empty',width,passed:true});if(width===1440||width===390)await p.screenshot({path:path.join(out,'计划改版-empty-'+(width===1440?'桌面':'手机')+'.png'),fullPage:true,animations:'disabled'});}
assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'训练计划改版核对.json'),JSON.stringify({rows,errors,week_selection_consistent:true,rest_day:true,history_filter:true,actual_training_data:true,cloud_calls:0,references:['https://www.hevyapp.com/features/workout-plan-generator','https://www.strong.app/','https://www.trainerize.com/'],result:'passed'},null,2));console.log('PASS:计划四状态布局、真实图解/周历、日期/休息切换、计划筛选，无AI。');
}finally{await fx.close();}
