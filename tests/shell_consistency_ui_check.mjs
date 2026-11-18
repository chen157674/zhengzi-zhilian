// 直接量测跨导航页面的几何一致性；不是仅检查CSS字符串。
import assert from 'node:assert/strict';import fs from 'node:fs';import path from 'node:path';import {BrowserFixture,root} from './formal_browser_support.mjs';
const f=new BrowserFixture('shell-consistency'),rows=[],errors=[],out=path.join(root,'第五轮优化/实现核对');
try{
 f.seed(`from fitness_app import create_app
from fitness_app.auth import initialize_admin
import os
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']});initialize_admin(app.extensions['database'],'Shell_Admin','shell-isolated-password');app.extensions['database'].dispose()`);
 f.start();const c=await f.browser('Edge',{width:1440,height:1000});await f.apiLogin(c,'Shell_Admin','shell-isolated-password');const p=await c.newPage();p.on('pageerror',e=>errors.push(e.message));
 const routes=[['/realtime','#live-stage'],['/video','#video-drop'],['/training/plan','#plan-empty'],['/training/plans/new','#plan-editor:not([hidden])'],['/training/review','#review-summary .review-metric'],['/coach/knowledge','.learn-lesson-card'],['/coach/nutrition','.food-category'],['/records?view=list','#record-rows'],['/settings','.settings-group'],['/account','#nickname:not([disabled])'],['/account/training-profile','#training-profile-fields:not([disabled])'],['/coach','#ai-chat-input']];
 for(const width of [1440,1280,768,390,320]){
  await p.setViewportSize({width,height:width<768?844:1000});let baseline=null;
  for(const [route,ready]of routes){
   await p.goto(f.origin+route);await p.locator(ready).first().waitFor();await p.evaluate(()=>document.fonts.ready);
   const row=await p.evaluate(()=>{const children=[...document.querySelector('main').children].filter(n=>n.getBoundingClientRect().width>0),n=children.find(n=>!n.classList.contains('training-subnav-wrap'))||children[0],b=n.getBoundingClientRect(),h=[...document.querySelectorAll('main h1')].find(n=>n.getClientRects().length),r=h?.getBoundingClientRect(),style=getComputedStyle(n),nav=document.querySelector('.topbar nav').getBoundingClientRect();return {outerWidth:b.width,x:b.x,innerLeft:b.x+parseFloat(style.paddingLeft),rightPadding:parseFloat(style.paddingRight),headingX:r?.x,headingY:r?.y,headingSize:h?getComputedStyle(h).fontSize:null,navX:nav.x,scrollWidth:document.documentElement.scrollWidth,viewport:innerWidth};});
   baseline??=row;assert(Math.abs(row.outerWidth-baseline.outerWidth)<1,route+' outer width '+width);assert(Math.abs(row.innerLeft-baseline.innerLeft)<1,route+' content inset '+width);assert(Math.abs(row.navX-baseline.navX)<1,route+' global navigation shift '+width);assert.equal(row.headingSize,width<768?'25px':'29px',route+' heading size');assert(row.scrollWidth<=width+1,route+' overflow');
   assert(Math.abs(row.headingX-row.innerLeft)<1,route+' heading left alignment');
   const summaries=await p.locator('summary:visible').allTextContents();assert(!summaries.some(x=>/计算依据|任务过程|统计口径|查看依据|详细解读与依据|查看输入状态/.test(x)),route+' explanation-only accordion remains');
   rows.push({route,width,...row});if(width===1440||width===390)await p.screenshot({path:path.join(out,'统一页面-'+route.replaceAll('/','-').replaceAll('?','-')+'-'+width+'.png'),fullPage:true,animations:'disabled'});
  }
 }
 assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'页面尺寸统一核对.json'),JSON.stringify({rows,errors,result:'passed',scope:'12 authenticated routes at five widths; actual DOM geometry and explanation controls',physical_phone:false,cloud_calls:0},null,2));console.log('PASS:12页×5宽度真实外框、左右留白、标题字号、主导航位置一致；无说明性折叠和横向溢出。');
}finally{await f.close()}
