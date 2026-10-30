"""内置生图头像只用于注册随机分配；用户自定义头像走本人上传。"""
import secrets

AVATARS=tuple({'id':f'avatar-{i:02d}','label':f'运动伙伴{i:02d}',
               'url':f'/static/avatars/avatar-{i:02d}.png'} for i in range(1,7))
AVATAR_IDS=tuple(x['id'] for x in AVATARS)


def avatar_url(avatar_id,avatar_key=None,version=0):
    if avatar_key:return f'/api/v1/account/avatar?v={version}'
    return next((x['url'] for x in AVATARS if x['id']==avatar_id),AVATARS[0]['url'])


def random_profile():
    return {'nickname':secrets.choice(('轻快的','专注的','元气的','稳稳的','清晨的','努力的','自在的','向阳的'))+
                       secrets.choice(('海獭','小鹿','熊猫','松鼠','小鲸','白鹭','山雀','小狐'))+str(secrets.randbelow(10000)).zfill(4),
            'avatar_id':secrets.choice(AVATAR_IDS)}
