"""保存已逐帧查看的教程片段及发布者单动作素材的开发分区。"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment,project_path
configure_environment()
from fitness_app.research_extract import atomic_json
import av

# 起止秒来自 step-22 接触表。动作标签不代表片段姿态正确。
REVIEWED=[
 ('i14hrj7l7CI',61,91,'train'),('qmFQTDYwpsg',92,108,'train'),('IXeRFh9nUwk',142,157,'train'),
 ('7bLivsAhDFY',43,51.5,'train'),('Hy5L3-QJETc',65,88,'train'),('d1QEddtoOq0',129,149,'train'),
 ('dC7jhEk-29A',58,72,'train'),('3frdI6LLJ3Y',37,41,'train'),('NBkMIWWkIKQ',170,183,'validation'),
 ('U5uW6jaOiLg',64,82,'train'),('SQ1WOlq5UqM',5,45,'validation'),
 ('hrh0K1Oo7Yc',198,205,'train'),('4Y8QgiT2-OA',60,78,'validation'),
 ('gx0RWT7WbmA',60,82,'train'),('I01erzk7FDk',69,89,'validation'),
 ('7BkgqzC6WsM',119,140,'train'),('4iNlLpIzT9w',3,22,'validation'),
 ('X-iV-cG8cYs',40,55,'train'),('K8o68rDbri0',10,29,'validation'),
 ('Y29xKcze8Ik',88,106,'validation'),('wnPX6Fwe-Fg',79,98,'validation'),
 ('-buTln7JnFw',25,45,'validation'),('LsMLd6Pwwvw',1,16,'validation'),
 ('FNvndC4Ov04',43,57,'validation'),('I4LyZpwysGI',80,110,'validation'),
 ('BqYsKRzc0wM',24,30,'train'),('JjyvW9IpBXw',65,95,'train'),
 ('awEEyL5zGvU',20,36,'train'),('P8Hx90LdGH4',35,41,'train'),
 ('P8Hx90LdGH4',51,58,'train'),('P8Hx90LdGH4',84,91,'train'),
 ('H4mVGHaK2f4',95,110,'train'),('Dbly77Jgbo8',205,232,'validation'),
 ('OOe_HrNnQWw',96,114,'validation'),('ScAYp-Gik_E',46,57,'train'),
]


def run():
    directory=ROOT/'data/datasets/development-movements'
    sources=json.loads((directory/'sources.json').read_text(encoding='utf-8'))['sources'];by={s['id']:s for s in sources};rows=[]
    def add(source,start,end,partition,method,crop=None,action=None):
        rows.append({'id':source['id']+f'-{round(start*1000)}-{round(end*1000)}','source_id':source['id'],
                     'action_id':action or source['action_id'],'start_ms':round(start*1000),'end_ms':round(end*1000),
                     'partition':partition,'group':source.get('source_group',source['id']),'crop':crop,
                     'source_sha256':source['sha256'],'review_method':method,'specific_error_ground_truth':False})
    for key,start,end,partition in REVIEWED:
        source=by['youtube-'+key]
        add(source,start,end,partition,'publisher_title_and_Codex_contact_sheet; bounded active demonstration; not human independent truth')
    for key,start,end in [('3frdI6LLJ3Y',27,36),('d1QEddtoOq0',29,35),('BqYsKRzc0wM',31,36)]:
        add(by['youtube-'+key],start,end,'train','Codex reviewed actual empty-hand explanation/rest; not a labelled exercise repetition',action='unknown')
    base=next(r for r in rows if r['source_id']=='youtube-U5uW6jaOiLg')
    rows.append({**base,'id':base['id']+'-fixed-view','crop':[.25,0,1,1],
                 'review_method':'fixed right 75% excludes lower-left talking inset; no person scoring or dynamic crop'})
    # 已有另一来源；保留视频隔离，原始视频整体双视图不能当成单人体。
    for key,start,end in [('dvids-639911',5,16),('dvids-636920',6,20),('dvids-636927',3,15),('dvids-636933',2,15)]:
        add(by[key],start,end,'train','reviewed fixed lower view; entire publisher collection assigned to train, never independent validation',crop=[.26,.5,.77,1.])
        rows[-1]['group']='public_dvids_collection'
    add(by['commons-pec-deck-rear-deltoid'],565,580,'validation','previously reviewed rear deltoid segment',action='reverse_pec_deck_fly')
    add(by['commons-cdc-chest-press'],18,30,'validation','previously reviewed CDC chest press')
    add(by['commons-bench-fitnessscape'],0,7.1,'validation','previously reviewed FitnessScape bench')
    add(by['commons-pullups-fitnessscape'],0,6,'validation','previously reviewed FitnessScape pull-up')
    add(by['commons-go4it-back'],360,380,'train','reviewed actual lat pulldown, NOT assisted pull-up; behind-neck variant',action='lat_pulldown')
    add(by['commons-lat-pulldown-mistakes'],.5,10.5,'validation','publisher short: lat pulldown, frequent cuts; include as difficult validation',action='lat_pulldown')
    for source in sources:
        if not source['id'].startswith('seven-'):continue
        with av.open(str(project_path(source['path']))) as video:duration=float(video.duration)/1000000
        add(source,.1*duration,.9*duration,source['partition'],'publisher single-exercise source label; trim 10% edges; coarse good/bad not specific error')
    groups={p:{r['group'] for r in rows if r['partition']==p} for p in ('train','validation')}
    if groups['train']&groups['validation']:raise ValueError('新增分区来源组交叉')
    # 同一原片的片段必须全部在同一分区。
    split={}
    for row in rows:
        previous=split.setdefault(row['source_sha256'],row['partition'])
        if previous!=row['partition']:raise ValueError('相同原片跨分区')
    target=directory/'gym-segments.json'
    if target.exists():
        previous=json.loads(target.read_text(encoding='utf-8'))
        rows.extend(r for r in previous['segments'] if r.get('segment_owner') not in (None,'core_curated'))
    atomic_json(target,{'schema_version':1,'reviewer':'Codex; no independent human review',
                        'scope':'development action-type labels only; no formal independent test claim',
                        'temporal_annotation_review':{'date':'2026-10-23','evidence':['data/verification/step-22/training-active-phase-detail.jpg','data/verification/step-22/y-raise-equipment-evidence.jpg'],
                        'changes':'Trimmed explanation/cutaway phases from training: d1QE seated rear fly now129–149s; cable Y3frd now37–41s loaded demonstration (27–36s empty-hand explanation separately negative); Bq bench24–30s; P8 chest press35–41/51–58/84–91s preserves visibly incorrect and correct active repetitions. Source labels are not independent posture-error ground truth.'},
                        'excluded_sources':[{'source_id':'youtube-i1fywF_g-1U','reason':'95–140s empty-hand explanation excluded; later 220–245s bench phase independently reviewed and registered separately with fixed view'}],
                        'segments':rows})
    print(json.dumps({'segments':len(rows),'train':sum(r['partition']=='train' for r in rows),'validation':sum(r['partition']=='validation' for r in rows),'actions':len({r['action_id'] for r in rows})}))


if __name__=='__main__':run()
