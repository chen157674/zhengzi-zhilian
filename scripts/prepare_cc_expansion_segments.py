"""登记已查看画面的新来源短片，按发布者整组分区，保留误标/不匹配说明。"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
from fitness_app.research_extract import atomic_json

# 视频编号、起止秒、分区、动作覆盖（None沿用来源标题）。不将字幕的对/错视作专家标签。
SEGMENTS=[
 ('hELTuLntsuw',2,36,'validation',None),('YRY1IIu-ODs',.5,8,'train',None),('_KtitR_vvo0',1,7.8,'train',None),
 ('sXpRryVEPNY',28,33,'validation',None),('sXpRryVEPNY',34,41,'validation',None),
 ('KMXl2iPUpWo',11,21,'validation',None),('KMXl2iPUpWo',28,37,'validation',None),
 ('3CKka5pB7J8',4,25,'train',None),('AZMA3fV8J3M',.5,9.5,'train',None),
 ('TM6se0vr1VA',10,21,'train',None),('TM6se0vr1VA',23,35,'train',None),
 ('JDBFzmUAFGw',25,41,'validation',None),('m_Y9K-OTkbs',28,45,'train',None),('GIjpoJ7lmqo',13,29,'validation',None),
 ('ktW6a4PMFKs',1,6,'train',None),('ktW6a4PMFKs',10,14,'train',None),('K3xKgmkYt4E',33,55,'validation',None),
 ('F9WsyO8mk_k',.3,5.8,'train',None),('Jdkfuia1cl8',1,6.5,'train',None),('Jdkfuia1cl8',10,13,'train',None),
 ('cHzjrHRv5gY',1,7,'train',None),('cHzjrHRv5gY',10,13,'train',None),
 ('p_IhKBEPdO0',26,57,'train','pec_deck_fly'),
 ('gtLbK3irtwA',3,36,'train',None),('v0y1ofwURX4',10,17,'train',None),('8S8effiKW4Q',5,15,'train',None),
 ('ebAwdqOdd8A',13,30,'holdout',None),
 ('n-ULs8GcdiE',25,36,'validation',None),('zhJdEIWv4D0',29,45,'validation',None),
 ('KXaUWOwQ_oI',1,6,'train',None),('KXaUWOwQ_oI',10,13,'train',None),
 ('IHZ0oxP3Y6s',1,8.5,'validation',None),('IHZ0oxP3Y6s',10,15,'validation',None),
 ('hTE9szvKTWs',3,15,'train',None),('hTE9szvKTWs',19,30,'train',None),('NLc1Kmzaj7s',20,36,'validation',None),
 ('wrPpexRhuZ0',43,51,'train',None),('_i8DCZidDD8',.6,6.8,'train',None),
 ('nU35lVLojjM',46,59,'validation',None),('YVuGFuaik2w',1,11,'train',None),('YVuGFuaik2w',26,40,'train',None),
 ('CSW5Axv_vWw',26,37,'validation',None),('CSW5Axv_vWw',45,58,'validation',None),('ajkMEmi7AN8',8,27,'train',None),
 # 原标题的Assisted Pull-Up实际是弹力带辅助，不冒充目录中的器械平台。
 ('NX6Dycuvvy0',1,10,'train','unknown'),('fl3zro7uzu4',4,24,'train','unknown'),
 ('7vTnfE6oiXk',126,166,'train',None),('SMsS2Qswq0o',29,49,'train',None),
 ('FKqiXZVTVS4',3,18,'validation',None),('FKqiXZVTVS4',26,42,'validation',None),('QjSv_3ae-64',3,42,'validation',None),
 ('VGb_fJ81yWc',.3,6.5,'train',None),('640LLT6wVXo',2,24,'train',None),('TDYs2OjvlbI',8,42,'train',None),
 ('X6Aoy-V2wjk',12,30,'holdout',None),('5rQksYAOnVM',.3,6.5,'validation',None),
 ('AsU_PHNj26E',8,56,'holdout',None),('jh3gS4Gvf7w',25,48,'validation',None),('qk5Szywcq4M',2,30,'train',None),
 ('SJHlBLt1nbA',84,95,'validation',None),('hG6sWbZmW1c',80,97,'train',None),
 ('fW6-ykp1cjk',29,53,'validation',None),('Ka-yi9zHp2Q',21,35,'train',None),
 ('LCBEioFGYq8',20,42,'train',None),('LCBEioFGYq8',48,72,'train',None),('dyLMJUOP3R4',.4,7.3,'train',None),
 ('p_uGVdQijEA',1,9,'train',None),('p_uGVdQijEA',10,16,'train',None),
 ('ZxgD_89klTQ',75,91,'train',None),('bGxl-bpoL2E',51,75,'train',None),('bGxl-bpoL2E',115,135,'train',None),
 ('dbJFrH0LokA',40,70,'validation',None),
 ('Mrvyq-LCE_A',102,113,'holdout','reverse_pec_deck_fly'),('nCraISdfAWE',31,45,'holdout','lat_pulldown'),
 ('PkmEwch_wXA',8,27,'train','unknown'),('IBIoBpNfUPk',1,8,'train','unknown'),('yDAJWdomgiU',31,54,'train','unknown'),
 ('agvdnl_rA2k',3,12,'train','dumbbell_bench_press'),('QxDp9YeoSF8',2,30,'train','dumbbell_bench_press'),
 ('A9ixPRRFpmY',16,66,'validation','incline_dumbbell_press'),('i1fywF_g-1U',220,245,'validation','dumbbell_bench_press'),
 ('3UNXPzP0QaY',15,40,'validation','forearm_plank'),('fBywcddsrvw',1,10,'train','forearm_plank'),
 ('fBywcddsrvw',13,21,'train','forearm_plank'),('XzZ0wrSMzxI',4,15,'validation','forearm_plank'),
 ('tkx2TDhsSUM',9,21,'train','jumping_jack'),('NeN8c-94EOo',.3,7.5,'train','jumping_jack'),
 ('WxfSl-MV8_Y',1,12,'validation','jumping_jack'),('GcfADxQr0kI',4,34,'train','jumping_jack'),
 ('_MHaDnfV2KU',10,34,'holdout','jumping_jack'),('bB1Jye7cxKY',16,41,'validation','reverse_lunge'),
 ('H4mVGHaK2f4',70,82,'train','unknown'),('P8Hx90LdGH4',10,18,'train','unknown'),
 ('tAQBdzlCMyU',2,26,'train','hammer_curl'),('KWUqgyUskfY',4,24,'train','hammer_curl'),
 ('G7hy-AxQwcQ',76,100,'train','hammer_curl'),
 ('bptdQtH7IQA',28,42,'validation','cable_chest_fly'),('TTvo_7knHa0',35,48,'validation','cable_chest_fly'),
 ('Gpd5Q-A0H8Q',43,64,'train','reverse_pec_deck_fly'),('rRd3hvdBkdM',2,23,'train','seated_cable_row'),
 ('2zl9O0nGdAg',1,12,'validation','dumbbell_chest_fly'),('Vm-GIqZEDsk',22,43,'holdout','dumbbell_chest_fly'),
 ('oM7lwgPFXf8',24,34,'validation','machine_chest_press'),('uuxOFnMyrCw',40,51,'holdout','machine_chest_press'),
 ('3lslbPgaHYQ',14,22,'train','reverse_lunge'),('LJQcRT8d2H4',59,77,'validation','reverse_lunge'),
]
CROPS={'A9ixPRRFpmY':[0,0,.34,1],'i1fywF_g-1U':[0,0,.62,1],
       'tAQBdzlCMyU':[.33,0,.78,1], # 保留真人完整运动，去除左侧题名与标志。
       'KWUqgyUskfY':[.38,0,.76,1], # 固定主视角，排除侧面背景人物/镜像。
       'Gpd5Q-A0H8Q':[.03,.05,.67,1], # 固定训练者视角，排除右侧教练；画外手腕仍不可评估。
       'wrPpexRhuZ0':[0,.38,1,1], # 保留俯身演示者，排除站立教练头肩；不补画外关节。
       '2zl9O0nGdAg':[0,.2,1,1]} # 去掉上方动作名，保留实际飞鸟的双臂及躯干。
EXCLUDED={'youtube-W2fSSWcNvHE':'Cable versus band apparatus remains unclear in sampled frames',
          'youtube-Gl-LZu3JHdc':'row/reverse-fly variation unclear; do not blindly follow title',
          'youtube-qU_5EzfOl9A':'row/reverse-fly variation needs finer review',
          'youtube-v7M6Y76yNA4':'sampled talking/holding phase does not establish continuous active interval',
          'youtube-PkmEwch_wXA':'straight-leg sit-up; used only as unsupported variation negative',
          'youtube-IBIoBpNfUPk':'Romanian/straight-leg hinge without floor starts; not conventional deadlift',
          'youtube-yDAJWdomgiU':'single-arm dumbbell row; not barbell row',
          'youtube-UH0LTot-muU':'3D anatomy animation, not real human footage; not used as real-video training/test evidence',
          'youtube-1rUrd6xxGJU':'selectorized low-row apparatus; cable versus lever structure not clear enough in reviewed views; defer category mapping',
          'youtube-v2ESoWx17tk':'mixed cable front/lateral/rear shoulder variations; not a cable Y-raise positive',
          'youtube-H_QkInZ6t9Q':'mixed dumbbell shoulder exercises with close-ups; not a cable Y-raise positive',
          'youtube-_Akq1z8i3fM':'mixed cable front/lateral/rear shoulder variations; not a cable Y-raise positive'}


def run():
    directory=ROOT/'data/datasets/development-movements';target=directory/'gym-segments.json'
    data=json.loads(target.read_text(encoding='utf-8'));by={r['id']:r for r in json.loads((directory/'sources.json').read_text(encoding='utf-8'))['sources']}
    rows=[r for r in data['segments'] if r.get('segment_owner')!='cc_expansion']
    for key,start,end,partition,override in SEGMENTS:
        source=by.get('youtube-'+key)
        if source is None:continue
        first,last=round(start*1000),round(end*1000)
        crop=CROPS.get(key)
        rows.append({'id':source['id']+f'-{first}-{last}-curated','source_id':source['id'],'source_sha256':source['sha256'],
                     'action_id':override or source['action_id'],'start_ms':first,'end_ms':last,'partition':partition,'group':source['source_group'],'crop':crop,
                     'segment_owner':'cc_expansion','review_method':'Codex inspected actual contact-sheet movement and apparatus; raw title does not override image',
                     'review_evidence':'data/verification/step-22/cc-expansion-review-*.jpg','specific_error_ground_truth':False})
    groups={p:{r['group'] for r in rows if r['partition']==p} for p in ('train','validation','holdout')}
    if groups['train']&groups['validation'] or groups['train']&groups['holdout'] or groups['validation']&groups['holdout']:raise ValueError('发布者分区交叉')
    data['segments']=rows;data['extra_source_review_exclusions']=EXCLUDED
    atomic_json(target,data);print(json.dumps({'segments':len(rows),'new_curated':sum(r.get('segment_owner')=='cc_expansion' for r in rows),'holdout_segments':sum(r['partition']=='holdout' for r in rows)}))


if __name__=='__main__':run()
