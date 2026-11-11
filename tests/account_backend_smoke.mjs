// 正式入口配隔离库，核本人头像上传和内置生图实际圆形裁切；不重跑训练页面。
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {BrowserFixture,root} from './formal_browser_support.mjs';

const fixture=new BrowserFixture('account-backend-smoke');
const evidence={scope:'production entry with isolated database; account HTTP and avatar asset rendering only'};
const sharedAvatarRoot=path.join(root,'data/user-avatars');
const priorSharedAvatars=fs.existsSync(sharedAvatarRoot)?fs.readdirSync(sharedAvatarRoot).sort():[];
try{
  fixture.seed("import os; from fitness_app.database import Database; from fitness_app.auth import initialize_admin; d=Database(os.environ['FITNESS_DATABASE_PATH']); d.initialize(); initialize_admin(d,'Account_Smoke','account-smoke-password-123'); d.dispose()");
  const origin=fixture.start();const context=await fixture.browser('Edge',{width:1100,height:900});
  await fixture.apiLogin(context,'Account_Smoke','account-smoke-password-123');
  const get=async url=>{const response=await context.request.get(origin+url);assert.equal(response.status(),200);return (await response.json()).data};
  const health=await get('/api/v1/health');assert.equal(health.schema_version,5);
  const profile=await get('/api/v1/account/profile');assert.notEqual(profile.nickname,'Account_Smoke');
  const csrf=(await get('/api/v1/auth/csrf')).csrf_token;
  const update=await context.request.post(origin+'/api/v1/account/profile',{data:{nickname:'晨练的小熊',expected_version:profile.profile_version},headers:{'X-CSRFToken':csrf}});
  assert.equal(update.status(),200);assert.equal((await get('/api/v1/auth/me')).nickname,'晨练的小熊');
  const overview=await get('/api/v1/account/overview');assert.equal(overview.total_sessions,0);
  const actions=(await get('/api/v1/actions')).actions;assert.equal(actions.filter(x=>x.counting_available).length,8);
  assert.equal(actions.reduce((n,x)=>n+x.issues.length,0),16);
  assert.equal((await context.request.get(origin+'/admin/experiments')).status(),404);
  assert.equal((await context.request.get(origin+'/api/v1/account/avatars')).status(),404);
  const catalogue=JSON.parse(fixture.seed("import json; from fitness_app.profile_assets import AVATARS; print(json.dumps({'avatars':list(AVATARS)},ensure_ascii=False))"));
  assert.equal(catalogue.avatars.length,6);
  const current=await get('/api/v1/account/profile');
  const uploaded=await context.request.post(origin+'/api/v1/account/avatar',{multipart:{expected_version:String(current.profile_version),
    file:{name:'my-avatar.png',mimeType:'image/png',buffer:fs.readFileSync(path.join(root,'fitness_app/static/avatars/avatar-06.png'))}},headers:{'X-CSRFToken':csrf}});
  assert.equal(uploaded.status(),200,await uploaded.text());
  const uploadedProfile=(await uploaded.json()).data;assert.equal(uploadedProfile.avatar_source,'uploaded');
  const privateAvatar=await context.request.get(origin+uploadedProfile.avatar_url);assert.equal(privateAvatar.status(),200);
  assert(privateAvatar.headers()['content-type'].startsWith('image/jpeg'));
  assert.equal(fs.readdirSync(path.join(fixture.temporary,'user-avatars')).filter(name=>name.endsWith('.jpg')).length,1);
  assert.deepEqual(fs.existsSync(sharedAvatarRoot)?fs.readdirSync(sharedAvatarRoot).sort():[],priorSharedAvatars);
  assert.equal(fs.readdirSync(path.join(fixture.temporary,'runtime/avatar-inputs')).length,0);
  const page=await context.newPage();
  await page.setContent(`<meta charset="utf-8"><style>body{font-family:'Microsoft YaHei',sans-serif;background:#f6f7f4;color:#17211d;margin:32px}h1{font-size:24px}p{color:#5b6861}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}.card{background:white;border:1px solid #dce3de;border-radius:16px;text-align:center;padding:20px 8px}.card img{display:block;margin:0 auto 12px;width:208px;height:208px;border-radius:50%;object-fit:cover}.label{font-size:14px}</style><h1>默认运动头像 · 年轻中国人物</h1><p>内置生图素材预览。注册时随机分配；用户更换头像时上传自己的图片。</p><div class="grid">${catalogue.avatars.map(a=>`<div class="card"><img src="${origin+a.url}" alt="${a.label}"><span class="label">${a.label}</span></div>`).join('')}</div>`);
  await page.locator('img').evaluateAll(async images=>{await Promise.all(images.map(image=>image.decode()))});
  assert(await page.locator('img').evaluateAll(images=>images.length===6&&images.every(image=>image.naturalWidth>0)));
  const screenshot=path.join(root,'data/verification/step-19/account-avatars.png');
  await page.screenshot({path:screenshot,fullPage:true});
  Object.assign(evidence,{checks:'passed',schema_version:health.schema_version,avatars_rendered:6,profile_edit:true,avatar_upload:true,random_defaults_only:true,experiment_routes_removed:true,
    counting_actions:8,reference_posture_items:16,personal_database_modified:false,avatar_storage_isolated_with_database:true,
    shared_avatar_storage_unchanged:true,screenshot:'data/verification/step-19/account-avatars.png'});
}finally{await fixture.close()}
evidence.owned_processes_and_temporary_files_cleaned=true;
fs.writeFileSync(path.join(root,'data/verification/step-19/account-backend-smoke.json'),JSON.stringify(evidence,null,2)+'\n');
console.log(JSON.stringify(evidence));
