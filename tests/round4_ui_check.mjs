// 第四轮真实页面检查；隔离身份与数据库，不调用物理相机或付费AI。
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fx=new BrowserFixture('round4-ui');
fx.env.FITNESS_ENABLE_CORRECTION_LABELS='1';
const output=path.resolve(root,process.env.FITNESS_UI_EVIDENCE_DIR||'第四轮优化/实现核对');assert(!path.relative(root,output).startsWith('..'));
const errors=[],writes=[];
fs.mkdirSync(output,{recursive:true});
try {
  fx.seed(`from fitness_app import create_app
from fitness_app.auth import initialize_admin
import os
app=create_app({'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
initialize_admin(app.extensions['database'],'Round4_Admin','round4-ui-test-password')
app.extensions['database'].dispose()`);
  fx.start();
  const context=await fx.browser('Edge',{width:1440,height:1000});
  const page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{
    if(request.method()==='POST'&&/\/api\/v1\/(ai|coach)\//.test(request.url()))writes.push(request.url());
  });
  await context.addInitScript(()=>{
    window.cameraRequests=0;
    if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=async()=>{
      window.cameraRequests++;
      throw new Error('本轮界面检查不使用物理摄像头');
    };
  });
  const shot=async name=>{
    await page.evaluate(()=>document.fonts.ready);
    await page.screenshot({path:path.join(output,name+'.png'),animations:'disabled',fullPage:true});
  };
  const bounds=async label=>{
    const size=await page.evaluate(()=>({viewport:innerWidth,width:document.documentElement.scrollWidth}));
    assert(size.width<=size.viewport+1,label+'：页面横向溢出');
    if(size.viewport<=767&&await page.locator('body[data-public-auth=false] .topbar nav').count()){const navigation=await page.locator('.topbar nav').boundingBox();const viewportHeight=await page.evaluate(()=>innerHeight);assert(navigation.y>=viewportHeight-110&&navigation.y+navigation.height<=viewportHeight+1,label+'：手机导航必须固定在视口底部');}
    for(const box of await page.locator('.topbar nav a:visible').evaluateAll(nodes=>nodes.map(node=>{
      const r=node.getBoundingClientRect();return {left:r.left,right:r.right,width:r.width,height:r.height};
    }))){
      assert(box.left>=-1&&box.right<=size.viewport+1,label+'：导航超出视口');
      assert(box.width>=44&&box.height>=44,label+'：导航点击区域过小');
    }
  };
  for(const [route,name] of [['/login','登录页'],['/register','注册页']]){
    await page.goto(fx.origin+route);
    assert.equal(await page.locator('.auth-form-brand').count(),1);
    assert.equal(await page.locator('.auth-switch a').count(),1);
    assert.equal(await page.locator('#username').getAttribute('name'),'email');
    assert.equal(await page.locator('#username-help').count(),0);
    assert.equal(await page.locator('nav[aria-label="主要导航"]').count(),0);
    assert.equal(await page.locator('.auth-backdrop').evaluate(image=>image.complete&&image.naturalWidth>0),true);
    for(const width of [6368,3184,1920,1440,1024,901,900,390,320]){
      await page.setViewportSize({width,height:width>2000?Math.round(width*1668/3184):width<768?844:1000});await bounds(route+' '+width);
      if(width>900){
        const placement=await page.evaluate(()=>{const copy=document.querySelector('.auth-copy').getBoundingClientRect(),art=document.querySelector('.auth-artwork').getBoundingClientRect(),visual=document.querySelector('.auth-visual').getBoundingClientRect(),form=document.querySelector('.auth-form-panel').getBoundingClientRect();return {clear:copy.bottom+12<art.top,split:Math.abs(visual.width/innerWidth-.48)<.01,cardAfterScene:form.left>visual.right};});
        assert(placement.clear&&placement.split&&placement.cardAfterScene,'认证页按参考分屏，文案与人物不能重叠');
        if(width>=1600){const proportion=await page.locator('.auth-form-panel').evaluate(node=>node.getBoundingClientRect().width/innerWidth);assert(proportion>.29&&proportion<.34,'宽屏表单须保持参考比例，不能封顶后缩成小块');}
      }
    }
    await page.setViewportSize({width:1440,height:1000});
    await bounds(route);await shot(name);
    await page.setViewportSize({width:320,height:740});
    await bounds(route+'手机');
    assert.equal(await page.locator('.auth-backdrop').isVisible(),false);
    await shot(name+'-窄屏');
    await page.setViewportSize({width:1440,height:1000});
  }
  await page.goto(fx.origin+'/register');
  await page.locator('.primary-submit').click();
  assert.equal(await page.locator('#auth-error-summary').isVisible(),true);
  assert.equal(await page.locator('#auth-error-summary').evaluate(node=>node===document.activeElement),true);
  await page.locator('#auth-error-summary a').first().click();
  assert.equal(await page.locator('#username').evaluate(node=>node===document.activeElement),true);
  await page.locator('#username').fill('not-supported@example.com');
  await page.locator('#password').fill('round4-created-user-password');
  await page.locator('#password_confirmation').fill('round4-created-user-password');
  await page.locator('.primary-submit').click();
  await page.locator('#email-error').filter({hasText:'常见个人邮箱'}).waitFor();
  await page.locator('#username').fill('round4_newuser@qq.com');
  await page.locator('#password').fill('round4-created-user-password');
  await page.locator('#password_confirmation').fill('round4-created-user-password');
  await page.locator('[data-toggle-password="password"]').click();
  assert.equal(await page.locator('#password').getAttribute('type'),'text');
  await Promise.all([page.waitForURL(/\/login\?notice=registered/),page.locator('.primary-submit').click()]);
  await page.goto(fx.origin+'/records?view=list');
  assert.match(page.url(),/\/login\?returnTo=/);
  await page.locator('#username').fill('round4_newuser@qq.com');
  await page.locator('#password').fill('round4-created-user-password');
  await Promise.all([page.waitForURL(/\/records\?view=list/),page.locator('.primary-submit').click()]);
  await page.locator('#account-menu summary').click();
  await Promise.all([page.waitForURL(/\/login$/),page.locator('[data-action="auth.logout"]').click()]);
  await fx.apiLogin(context,'Round4_Admin','round4-ui-test-password');
  const routes=[['/','首页'],['/realtime','实时准备'],['/video','视频选择'],['/records','训练概览'],['/records?view=list','训练记录'],['/coach','AI教练'],['/account','个人中心'],['/settings','设置'],['/account/ai','AI服务设置'],['/account/training-profile','训练档案'],['/account/sessions','登录管理'],['/account/password','修改密码'],['/admin/users','账号管理'],['/actions','动作选择'],['/prepare?action=barbell_overhead_press','备用训练准备']];
  for(const [route,name]of routes){
    await page.goto(fx.origin+route);
    assert.deepEqual(await page.locator('.topbar nav a').allTextContents(),['首页','实时分析','视频分析','我的训练','健身指南']);
    assert.equal(await page.locator('#ai-assistant').count(),1);
    assert.equal(await page.locator('body').evaluate(node=>getComputedStyle(node).backgroundColor),'rgb(252, 248, 242)');
    await bounds(route);
    await page.waitForFunction(()=>[...document.querySelectorAll('fieldset')].filter(node=>node.getClientRects().length).every(node=>!node.disabled));
    if(route==='/records')await page.locator('#overview-loading').waitFor({state:'hidden'});
    if(route==='/records?view=list')await page.waitForFunction(()=>!document.querySelector('#record-rows').textContent.includes('正在读取'));
    if(route==='/'){
      await page.waitForFunction(()=>document.querySelector('#showcase-video').readyState>=2);
      const placement=await page.locator('#showcase-player').evaluate(node=>{
        const stage=node.getBoundingClientRect(),tools=node.querySelector('.showcase-tools').getBoundingClientRect();
        return {gap:stage.bottom-tools.bottom,top:tools.top-stage.top,height:stage.height};
      });
      assert(placement.gap>=0&&placement.gap<=20&&placement.top>placement.height/2,'首页播放控件应在视频下方角落');
    }
    if(route==='/realtime'){
      assert.equal(await page.locator('#live-selection').inputValue(),'');
      await page.locator('#live-action-search-panel summary').click();
      await page.locator('#live-action-search').fill('不存在的动作名称');
      assert.equal(await page.locator('.action-search-result').count(),0);
      await page.locator('#live-action-search').fill('弯举');
      const chosen=await page.locator('.action-search-result').first().innerText();
      await page.locator('.action-search-result').first().click();
      await page.waitForFunction(()=>document.querySelector('#live-selection').value!=='');
      assert.match(await page.locator('#live-action').innerText(),new RegExp(chosen));
      await page.locator('#live-mode-auto').click();
    }
    if(route==='/account'){
      await page.locator('#nickname:not([disabled])').waitFor();
      await page.waitForFunction(()=>document.querySelector('#profile-recent').dataset.state!=='loading');
      assert.equal(await page.locator('.account-link').count(),2);
      assert.equal(await page.locator('.profile-training .security-row').count(),0,'训练入口不再沿用套框旧行');
      await page.locator('#nickname').fill('布局核对用户');await page.locator('#profile-save').click();
      await page.locator('#global-message').filter({hasText:'昵称已更新'}).waitFor();
      await page.locator('#nickname').fill('未保存的昵称');await page.locator('.account-link').first().click();
      await page.locator('#confirm-dialog[open] [data-dialog-cancel]').click();assert.equal(await page.locator('#nickname').inputValue(),'未保存的昵称');
      await page.locator('#profile-cancel').click();assert.equal(await page.locator('#nickname').inputValue(),'布局核对用户');
      await page.locator('#global-message').waitFor({state:'hidden'});
    }
    if(route==='/actions'){
      await page.locator('#action-search').fill('弯举');assert(await page.locator('.action-card:visible').count()>0);
      await page.locator('.action-card:visible').first().click();assert(await page.locator('#action-next').isEnabled());
      await page.locator('#clear-search').click();
    }
    if(route==='/account/ai'){
      assert.equal(await page.locator('#ai-personal-row').isVisible(),false,'系统来源不展开个人密钥表单');
      await page.locator('#ai-source').selectOption('personal');assert.equal(await page.locator('#ai-personal-row').isVisible(),true);
      await page.locator('#ai-personal-key').fill('sk-isolated-round4-style-fixture-2026');
      await page.locator('#ai-settings-save').click();await page.locator('#global-message').filter({hasText:'AI设置已保存'}).waitFor();
      assert.equal(await page.locator('#ai-personal-key').inputValue(),'');
      await page.reload();await page.locator('#ai-settings-fields:not([disabled])').waitFor();assert.equal(await page.locator('#ai-source').inputValue(),'personal');
      await page.locator('#ai-source').selectOption('system');await page.locator('#ai-settings-save').click();
      await page.waitForFunction(()=>document.querySelector('#ai-settings-fields').disabled===false);
      assert.equal(await page.locator('#ai-personal-row').isVisible(),false);
    }
    if(route==='/account/training-profile'){
      assert.equal(await page.locator('#training-more').evaluate(node=>node.open),false);
      assert.equal(await page.locator('#training-height').isVisible(),true);
      assert.equal(await page.locator('#training-weight').isVisible(),true);
      assert.equal(await page.locator('#training-minutes').isVisible(),false);
    }
    if(route==='/settings'){
      assert.equal(await page.locator('a[href="/account/correction-labels"]').count(),0);
      const columns=await page.locator('.settings-hub-grid').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length);assert.equal(columns,2,'桌面设置应使用并列分组，不能堆成过长卡片');
    }
    if(route==='/records?view=list'){
      assert(await page.locator('[data-action="history.new"]').isVisible(),'开始新分析不能被旧样式隐藏');
      assert.equal(await page.locator('#record-filters').evaluate(node=>getComputedStyle(node).display),'grid');
    }
    await shot(name);
    for(const width of [1280,1024,768,767,390,320]){
      await page.setViewportSize({width,height:width<500?844:1000});
      await bounds(route+' '+width);
      if(route==='/video'){
        const entry=await page.locator('#video-drop').evaluate(node=>{const box=node.getBoundingClientRect(),button=node.querySelector('#video-select').getBoundingClientRect(),mark=node.querySelector('.upload-mark').getBoundingClientRect(),svg=node.querySelector('.upload-mark svg'),path=svg.querySelector('path'),ink=path.getBBox(),center=new DOMPoint(ink.x+ink.width/2,ink.y+ink.height/2).matrixTransform(path.getScreenCTM());return {buttonInside:button.left>=box.left&&button.right<=box.right,centered:Math.abs(center.x-(box.left+box.width/2))<1&&Math.abs(center.y-(mark.top+mark.height/2))<1};});
        assert(entry.buttonInside&&entry.centered,'保留的上传图案、文字和按钮应共用中轴，图案路径须实际居中');
        const copyOrder=await page.locator('.video-demo-card:visible').evaluateAll(nodes=>nodes.map(node=>{const title=node.querySelector('h3').getBoundingClientRect(),action=node.querySelector('.demo-card-action').getBoundingClientRect();return title.bottom<=action.top+1;}));assert(copyOrder.every(Boolean),'演示标题与操作不能拼在同一行');
        const cards=await page.locator('.video-demo-card:visible').evaluateAll(nodes=>nodes.map(node=>{const card=node.getBoundingClientRect(),copy=node.querySelector('div').getBoundingClientRect(),parent=node.parentElement.getBoundingClientRect();return {cardWidth:card.width,copyWidth:copy.width,cardHeight:card.height,parentWidth:parent.width};}));
        assert(cards.every(card=>card.cardWidth>=240&&card.copyWidth>=90&&card.cardHeight<300),'演示卡不能被旧网格列位挤成单字竖排');
        if(width<=767)assert(cards.every(card=>Math.abs(card.cardWidth-card.parentWidth)<2),'手机演示卡各占完整一行');
      }
      if(route==='/records?view=list'){
        const geometry=await page.locator('#record-filters').evaluate(form=>{const parent=form.getBoundingClientRect();const controls=[...form.querySelectorAll('input,select,button')].filter(node=>node.getClientRects().length).map(node=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom};});let overlap=false;for(let i=0;i<controls.length;i++)for(let j=i+1;j<controls.length;j++){const a=controls[i],b=controls[j];if(Math.min(a.right,b.right)-Math.max(a.x,b.x)>1&&Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)>1)overlap=true;}return {overlap,inside:controls.every(r=>r.x>=parent.left&&r.right<=parent.right)};});
        assert.equal(geometry.overlap,false,'筛选输入与下拉框不能重叠');assert.equal(geometry.inside,true,'控件必须处于筛选区域内');
        assert(await page.locator('#records-table .table-empty').evaluate(node=>{const r=node.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth+1;}),'空态提示和入口不能藏在横向表格屏外');
      }
      if(route==='/admin/users'&&width<=767){
        const actions=await page.locator('#account-rows tr[data-user-id] .row-actions').evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right};}));assert(actions.length>0&&actions.every(r=>r.left>=0&&r.right<=width),'账号状态与操作不能藏到横向屏外');
      }
      if(route==='/account'){
        const rows=await page.locator('.account-link').evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect(),icon=node.querySelector('.account-link-icon').getBoundingClientRect(),copy=node.querySelector('.account-link-copy').getBoundingClientRect(),arrow=node.querySelector('.account-link-chevron').getBoundingClientRect();return {height:r.height,contentInset:copy.left-r.left,iconRight:icon.right,textLeft:copy.left,centerGap:Math.abs((arrow.top+arrow.bottom)/2-(r.top+r.bottom)/2),border:getComputedStyle(node).borderTopWidth};}));
        assert(rows.every(row=>row.height>=72&&row.height<=92&&row.contentInset>=50&&row.textLeft>row.iconRight&&row.centerGap<2&&row.border==='0px'),'训练入口应为紧凑对齐整行，不再卡片套卡片或文字贴边');
        if(width<=767)assert.equal(await page.locator('.profile-edit-grid').evaluate(node=>getComputedStyle(node).gridTemplateColumns.split(' ').length),1,'手机头像和表单不能挤成两列');
        const placement=await page.evaluate(()=>{const recent=document.querySelector('#profile-page>.profile-recent').getBoundingClientRect(),grid=document.querySelector('.profile-grid').getBoundingClientRect();return recent.top>=grid.bottom;});assert(placement,'最近训练独立放到整页下方');
      }
      if(route==='/realtime'){
        await page.locator('#live-action-search-panel summary').click();
        const popup=await page.locator('.action-search-popup').boundingBox();
        assert(popup.x>=0&&popup.x+popup.width<=width+1,'动作搜索弹层超出视口');
        await page.locator('#live-action-search').press('Escape');
        assert.equal(await page.locator('#live-action-search-panel').evaluate(node=>node.open),false);
      }
      if(route==='/coach'){
        assert.equal(await page.locator('#ai-assistant').isVisible(),true);
        const flow=await page.evaluate(()=>{const welcome=document.querySelector('#ai-chat-welcome').getBoundingClientRect(),form=document.querySelector('#ai-chat-form').getBoundingClientRect();return {welcomeEnd:welcome.bottom,formStart:form.top};});assert(flow.formStart>=flow.welcomeEnd-1,'欢迎内容和推荐问题不能被输入框盖住');
        assert.equal(await page.locator('#ai-assistant').getAttribute('aria-modal'),null);
        assert.equal(await page.locator('main').evaluate(node=>node.inert),false);
        await page.locator('#ai-chat-input').fill('这次先关注什么？');
        assert.equal(await page.locator('#ai-chat-send').isEnabled(),true);
        await page.locator('#ai-chat-input').fill('');
      }
      if(width===390)await shot(name+'-窄屏');
    }
    assert.equal(await page.evaluate(()=>window.cameraRequests),0,'页面不应自动开启摄像头');
    await page.setViewportSize({width:1440,height:1000});
  }
  assert.deepEqual(writes,[],'访问页面不应自动创建AI任务或消费');
  assert.deepEqual(errors,[],'页面脚本异常');
  console.log('第四轮'+routes.length+'个登录后页面、认证、六档宽度、素材、AI容器及无自动调用检查通过。');
} finally {
  await fx.close();
}
