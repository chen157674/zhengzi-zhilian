const root=document.querySelector('#camera-preparation'),button=document.querySelector('#legacy-fullscreen');
button.onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await root.requestFullscreen();}catch{root.classList.toggle('page-expanded');}};
document.addEventListener('fullscreenchange',()=>{button.textContent=document.fullscreenElement?'退出全屏':'全屏';});
document.addEventListener('keydown',e=>{if(e.key==='Escape')root.classList.remove('page-expanded');});
