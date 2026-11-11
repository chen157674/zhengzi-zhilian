// 按采用稿组织四个动效节拍；静止态始终完整，演示骨架继续由 home_demo.js 同帧绘制。
const reduced=matchMedia('(prefers-reduced-motion: reduce)');
const desktop=matchMedia('(min-width: 768px)');
const shell=document.querySelector('.landing-home');
const demo=document.querySelector('.showcase-player');
const detail=document.querySelector('.home-detail');
const sections=[...document.querySelectorAll('[data-home-section]')];
const motions=new Set();
const seen=new Set();
const visible=new Set();
let observer=null,frame=null,disposed=false;

function animate(node,keyframes,options={}){
  if(!node||reduced.matches||disposed||typeof node.animate!=='function')return;
  const motion=node.animate(keyframes,{duration:720,easing:'cubic-bezier(.2,.8,.2,1)',...options});
  motions.add(motion);
  motion.finished.then(()=>motions.delete(motion),()=>motions.delete(motion));
}
function reveal(section){
  if(seen.has(section)||reduced.matches)return;
  seen.add(section);
  const kind=section.dataset.homeSection;
  if(kind==='detail'){
    // 先呈现照片，再画出关节连线，最后展开真实取自同一张图的局部放大。
    const figure=section.querySelector('.home-detail-visual');
    animate(figure,[{clipPath:'inset(0 0 7% 0 round 24px)'},{clipPath:'inset(0 0 0 0 round 24px)'}],{duration:900});
    for(const path of section.querySelectorAll('.home-detail-overlay .detail-pose-line')){
      const length=path.getTotalLength();
      animate(path,[{strokeDasharray:String(length),strokeDashoffset:String(length)},{strokeDasharray:String(length),strokeDashoffset:'0'}],{delay:180,duration:850});
    }
    animate(section.querySelector('.home-detail-loupe'),[{transform:'scale(.76)',opacity:0},{transform:'scale(1)',opacity:1}],{delay:320,duration:780});
    animate(section.querySelector('.home-detail-cue'),[{transform:'translateY(10px)',opacity:0},{transform:'translateY(0)',opacity:1}],{delay:430,duration:500});
  }else if(kind==='analysis'){
    // 共边面板由左至右展开，使画面与建议建立关联。
    animate(section.querySelector('.home-analysis-stage'),[{clipPath:'inset(0 8% 0 0 round 24px)'},{clipPath:'inset(0 0 0 0 round 24px)'}],{duration:900});
    animate(section.querySelector('.home-analysis-feedback'),[{transform:'translateX(28px)',opacity:.35},{transform:'translateX(0)',opacity:1}],{delay:120,duration:740});
  }else{
    // 收尾只保留一次小幅景别推进，入口始终可以直接点击。
    animate(section.querySelector('.home-closing-photo'),[{transform:'scale(1.045)'},{transform:'scale(1)'}],{duration:1200});
    animate(section.querySelector('.home-closing-copy'),[{transform:'translateY(16px)',opacity:.65},{transform:'translateY(0)',opacity:1}],{duration:650});
  }
}
function paintScroll(){
  frame=null;
  if(disposed||reduced.matches||!desktop.matches||!shell)return;
  if(demo&&visible.has(demo)){
    const rect=demo.getBoundingClientRect();
    const progress=Math.max(0,Math.min(1,(innerHeight-rect.top)/(innerHeight+rect.height)));
    shell.style.setProperty('--home-demo-scale',String(.985+.015*Math.min(1,progress*2)));
  }
  if(detail&&visible.has(detail)){
    const rect=detail.getBoundingClientRect();
    const progress=Math.max(0,Math.min(1,(innerHeight-rect.top)/(innerHeight+rect.height)));
    shell.style.setProperty('--home-photo-shift',((.5-progress)*18).toFixed(2)+'px');
    shell.style.setProperty('--home-detail-scale',String(1.035+progress*.015));
  }
}
function requestScroll(){
  if(frame===null&&!disposed&&!reduced.matches&&desktop.matches)frame=requestAnimationFrame(paintScroll);
}
function observe(){
  observer?.disconnect();
  visible.clear();
  if(disposed||reduced.matches||!('IntersectionObserver' in window))return;
  observer=new IntersectionObserver(entries=>{
    for(const entry of entries){
      if(entry.isIntersecting){
        visible.add(entry.target);
        if(entry.target!==demo)reveal(entry.target);
      }else visible.delete(entry.target);
    }
    requestScroll();
  },{threshold:.12});
  if(demo)observer.observe(demo);
  sections.forEach(section=>observer.observe(section));
}
function reset(){
  for(const motion of motions)motion.cancel();
  motions.clear();
  if(frame!==null)cancelAnimationFrame(frame);
  frame=null;
  for(const name of ['--home-demo-scale','--home-photo-shift','--home-detail-scale'])shell?.style.removeProperty(name);
  observe();
}
reduced.addEventListener('change',reset);
desktop.addEventListener('change',reset);
addEventListener('scroll',requestScroll,{passive:true});
addEventListener('resize',requestScroll,{passive:true});
addEventListener('pagehide',()=>{disposed=true;reset();});
addEventListener('pageshow',event=>{if(event.persisted){disposed=false;seen.clear();reset();}});
observe();
animate(document.querySelector('.hero-intro'),[{transform:'translateY(12px)',opacity:.65},{transform:'translateY(0)',opacity:1}],{duration:600});
