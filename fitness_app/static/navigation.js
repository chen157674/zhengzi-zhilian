// 主导航子菜单：保留原生链接和既有data-nav离开检查。
const groups=[...document.querySelectorAll('details[data-nav-group]')];
const trigger=group=>group.querySelector('summary');
const links=group=>[...group.querySelectorAll('.nav-group-panel a')];
function close(group,restore=false){group.open=false;trigger(group).setAttribute('aria-expanded','false');if(restore)trigger(group).focus();}
for(const group of groups){
 trigger(group).setAttribute('aria-expanded',String(group.open));
 group.addEventListener('toggle',()=>{
  trigger(group).setAttribute('aria-expanded',String(group.open));
  if(group.open){for(const other of groups)if(other!==group)close(other);const account=document.getElementById('account-menu');if(account)account.open=false;}
 });
 trigger(group).addEventListener('keydown',event=>{
  if(!['ArrowDown','ArrowUp'].includes(event.key))return;
  event.preventDefault();group.open=true;trigger(group).setAttribute('aria-expanded','true');
  for(const other of groups)if(other!==group)close(other);
  const items=links(group);(event.key==='ArrowUp'?items.at(-1):items[0])?.focus();
 });
 group.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&group.open){event.preventDefault();event.stopPropagation();close(group,true);return;}
  if(!event.target.closest('.nav-group-panel'))return;
  const items=links(group),index=items.indexOf(document.activeElement);let next;
  if(event.key==='ArrowDown')next=(index+1)%items.length;
  if(event.key==='ArrowUp')next=(index+items.length-1)%items.length;
  if(event.key==='Home')next=0;if(event.key==='End')next=items.length-1;
  if(next!==undefined){event.preventDefault();items[next]?.focus();}
 });
 // 聚焦过程中的activeElement可能短暂为body，用relatedTarget判断是否真的离开菜单。
 group.addEventListener('focusout',event=>{if(group.open&&event.relatedTarget&&!group.contains(event.relatedTarget))close(group);});
 // 让原生链接先完成激活，再收起菜单；既有离开检查仍由app.js处理。
 for(const link of links(group))link.addEventListener('click',()=>setTimeout(()=>close(group),0));
}
document.addEventListener('pointerdown',event=>{for(const group of groups)if(group.open&&!group.contains(event.target))close(group);});
window.addEventListener('pagehide',()=>{for(const group of groups)close(group);});
window.addEventListener('fitness-auth-revoked',()=>{for(const group of groups)close(group);});
