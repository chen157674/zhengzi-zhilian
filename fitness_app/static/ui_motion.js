// 短促反馈跟随操作，不等待动画后才发送请求或导航。
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const motions=new Set();
function animate(node,frames,options={}){
  if(!node||reduced.matches)return;
  const motion=node.animate(frames,{duration:260,easing:'cubic-bezier(.2,.8,.2,1)',...options});
  motions.add(motion);motion.finished.then(()=>motions.delete(motion),()=>motions.delete(motion));
}
const menu=document.querySelector('#account-menu');
menu?.addEventListener('keydown',event=>{if(event.key==='Escape'){menu.open=false;menu.querySelector('summary').focus();}});
menu?.addEventListener('toggle',()=>{if(menu.open)animate(menu.querySelector('.account-dropdown'),[{opacity:.4,transform:'translateY(-6px) scale(.98)'},{opacity:1,transform:'none'}]);});
// 导航直接呈现页面框架；动效仅用于菜单和操作反馈，不让整页后退淡入。
document.addEventListener('pointerdown',event=>{
  const button=event.target.closest('button:not(:disabled),a.button');
  if(button)button.classList.add('is-pressed');
});
function releasePress(){document.querySelectorAll('.is-pressed').forEach(node=>node.classList.remove('is-pressed'));}
document.addEventListener('pointerup',releasePress);document.addEventListener('pointercancel',releasePress);window.addEventListener('blur',releasePress);
document.addEventListener('click',event=>{
  const link=event.target.closest('a[data-nav]');
  if(!link||event.defaultPrevented||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||event.button!==0||window.fitnessLeaveGuard)return;
  link.setAttribute('aria-busy','true');document.documentElement.classList.add('navigation-pending');
});
window.addEventListener('pageshow',()=>{document.documentElement.classList.remove('navigation-pending');document.querySelectorAll('a[data-nav][aria-busy]').forEach(a=>a.removeAttribute('aria-busy'));});
function settle(){if(reduced.matches){for(const motion of motions)motion.cancel();motions.clear();}}
reduced.addEventListener('change',settle);
window.addEventListener('pagehide',()=>{for(const motion of motions)motion.cancel();motions.clear();releasePress();});
