import {beforeLeave} from './app.js';
const page=document.getElementById('practice-prepare-page'),button=document.getElementById('prepare-live-start');
button.onclick=async()=>{
  if(button.disabled)return;
  const href='/realtime?'+new URLSearchParams({action:page.dataset.actionId});
  button.disabled=true;
  try{
    if(!await beforeLeave(href))return;
    sessionStorage.setItem('fitness-camera-intent',JSON.stringify({user:document.body.dataset.userId,action:page.dataset.actionId,created:Date.now()}));
    location.assign(href);
  }catch{document.getElementById('prepare-error').textContent='请从实时分析页面开启训练。';}
  finally{button.disabled=false;}
};
