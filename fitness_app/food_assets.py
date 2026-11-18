"""公共食物素材目录；只发布已复核且身份一致的本地WebP。"""
import hashlib
import json
from functools import lru_cache
from .paths import ROOT,project_path

@lru_cache(maxsize=1)
def food_catalog():
    return json.loads((ROOT/'fitness_app/static/food/catalog.json').read_text(encoding='utf-8'))

@lru_cache(maxsize=1)
def published_food_assets():
    folder=ROOT/'fitness_app/static/food'
    manifest=json.loads((folder/'sources.json').read_text(encoding='utf-8'))
    identities={x['id']:x for x in manifest['images']};assets=[]
    for item in food_catalog()['assets']:
        source=identities.get(item['id'])
        if not source or source.get('review_status')!='reviewed':continue
        web=source['web_asset']
        if web['file']!=item['id']+'.webp':continue
        path=project_path(folder/web['file'])
        if path.parent!=folder or not path.is_file() or path.stat().st_size!=web['bytes']:continue
        if hashlib.sha256(path.read_bytes()).hexdigest()!=web['sha256']:continue
        assets.append({**item,'url':'/static/food/'+web['file'],'width':1200,'height':800})
    return {'version':food_catalog()['version'],'assets':assets}

def food_asset_for(name,group=None):
    name=str(name or '').casefold()
    assets=published_food_assets()['assets']
    matches=[(len(alias),asset) for asset in assets if not any(x.casefold() in name for x in asset.get('excludes',[])) for alias in asset['aliases'] if alias.casefold() in name]
    if matches:return max(matches,key=lambda x:x[0])[1]
    if name.strip():return None
    return next((asset for asset in assets if group and asset.get('fallback')==group),None)
