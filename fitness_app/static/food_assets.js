import {api} from './app.js';
let assets=[],loading=null;
export function loadFoodAssets(){
 if(!loading)loading=api('/api/v1/nutrition/food-assets').then(data=>{assets=data.assets;return assets;}).catch(()=>{loading=null;console.warn('食物素材目录读取失败，暂用已有分类素材');return [];});
 return loading;
}
export function resolveFoodAsset(name,group){
 const text=String(name||'').toLocaleLowerCase();let selected=null,length=0;
 for(const asset of assets){if((asset.excludes||[]).some(word=>text.includes(word.toLocaleLowerCase())))continue;for(const alias of asset.aliases){if(alias.length>length&&text.includes(alias.toLocaleLowerCase())){selected=asset;length=alias.length;}}}
 if(selected)return selected;
 if(text.trim())return null;
 const category=assets.find(asset=>group&&asset.fallback===group);if(category)return category;
 const defaults={staple:['staple-foods','主食搭配'],protein:['protein-foods','鱼肉蛋豆搭配'],vegetable:['broccoli','蔬菜搭配'],meal:['balanced-meal','一餐搭配']};
 const fallback=defaults[group];return fallback?{id:fallback[0],label:fallback[1],url:'/static/food/'+fallback[0]+'.webp',width:1200,height:800}:null;
}
export function foodPicture(asset,{className='',lazy=true}={}){
 if(!asset)return null;const image=document.createElement('img');image.src=asset.url;image.alt=asset.label+'示意';image.width=asset.width;image.height=asset.height;image.decoding='async';if(lazy)image.loading='lazy';if(className)image.className=className;return image;
}
