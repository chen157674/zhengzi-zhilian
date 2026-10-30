"""18-D本地预生成语音资源；没有文件或没有对应内容审核时不宣称可播放。"""
import hashlib
import re

from .paths import ROOT,project_path
from .presentation_assets import MANIFEST,_read,_valid
from .catalogue import get_action
from .auth import APIError
from .database import json_text

STATE_PROMPTS={'training.started':'running','training.resting':'resting','tracking.waiting':'waiting'}
DEFAULTS={'cooldown_ms':5000,'static_confirm_ms':1000,'max_event_age_ms':1000,'max_audio_ms':15000,'max_audio_bytes':10485760}


def voice_catalogue(action_id):
    action=get_action(action_id)
    if not action:raise APIError('VALIDATION_FAILED','请选择已有动作',422)
    result={'schema_version':1,'action_id':action_id,'capability_version':action['capability_version'],'prompts':{},**DEFAULTS}
    try:
        stat=MANIFEST.stat();config=_read(stat.st_mtime_ns,stat.st_size).get('voice',{})
        if not isinstance(config,dict) or config.get('schema_version')!=1 or not isinstance(config.get('prompts'),dict):raise ValueError('语音清单版本无效')
        for key in DEFAULTS:
            value=config.get(key)
            if type(value) is not int or value<=0:raise ValueError('语音调度参数无效')
            result[key]=value
        verified={i['code'] for i in action['issues'] if i['status']=='verified'}
        for key,item in config.get('prompts',{}).items():
            if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z_.]{1,63}',key) or not isinstance(item,dict):continue
            text=item.get('text');kind=item.get('kind');priority=item.get('priority')
            if not isinstance(text,str) or not text.strip() or len(text)>80 or kind not in {'state','quality'} or type(priority) is not int or not 0<=priority<=100:continue
            output={'id':key,'text':text,'kind':kind,'priority':priority,'available':False,'status':'missing','url':None,
                'expected_filenames':[key+'.mp3',key+'.wav']}
            result['prompts'][key]=output
            if kind=='state' and key not in STATE_PROMPTS or kind=='quality' and key not in verified:
                output['status']='definition_unverified';continue
            folder=ROOT/'fitness_app/static/audio';candidates=[folder/(key+suffix) for suffix in ('.mp3','.wav') if (folder/(key+suffix)).exists()]
            if len(candidates)>1:output['status']='ambiguous_files';continue
            if not candidates:continue
            path=project_path(candidates[0]);path.relative_to(folder)
            stat=path.stat()
            if not path.is_file() or not 0<stat.st_size<=result['max_audio_bytes']:output['status']='invalid_file';continue
            review=item.get('audio_review') or {}
            if not isinstance(review,dict):output['status']='review_required';continue
            text_sha=hashlib.sha256(text.encode()).hexdigest();audio_sha=review.get('sha256')
            if review.get('decoded') is not True or review.get('content_matches_text') is not True or review.get('text_sha256')!=text_sha or not isinstance(audio_sha,str) or not re.fullmatch(r'[a-f0-9]{64}',audio_sha):
                output['status']='review_required';continue
            duration=review.get('duration_ms')
            if type(duration) is not int or not 0<duration<=result['max_audio_ms']:output['status']='invalid_duration';continue
            if not _valid(str(path),stat.st_mtime_ns,stat.st_size,stat.st_size,audio_sha):output['status']='review_stale';continue
            output.update(available=True,status='ready',duration_ms=duration,url=f'/static/audio/{path.name}?v={audio_sha[:12]}')
    except (OSError,ValueError,KeyError,TypeError):
        result['prompts']={};result['configuration_error']=True
    result['available_count']=sum(p['available'] for p in result['prompts'].values())
    result['catalogue_version']=hashlib.sha256(json_text(result).encode()).hexdigest()[:16]
    return result
