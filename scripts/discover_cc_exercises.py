"""使用公开视频站点实际提供的CC筛选发现候选；下载前仍逐片核实发布页许可。"""
import sys,json,re,urllib.request,urllib.parse,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fitness_app.paths import ROOT,configure_environment
configure_environment()
QUERIES={
    'cable_y_raise':'cable Y raise', 'dumbbell_chest_fly':'dumbbell chest fly',
    'cable_chest_fly':'cable chest fly','assisted_pull_up':'assisted pull up machine',
    'seated_cable_row':'seated cable row','overhead_dumbbell_extension':'dumbbell overhead triceps extension',
    'dumbbell_lateral_raise':'dumbbell lateral raise','cable_face_pull':'cable face pull',
    'cable_triceps_pushdown':'cable triceps pushdown','reverse_pec_deck_fly':'reverse pec deck fly',
    'dumbbell_front_raise':'dumbbell front raise','hammer_curl':'dumbbell hammer curl',
    'bent_over_dumbbell_reverse_fly':'bent over dumbbell reverse fly',
    'barbell_bench_press':'barbell bench press','dumbbell_bench_press':'dumbbell bench press',
    'incline_dumbbell_press':'incline dumbbell bench press','machine_chest_press':'machine chest press',
    'pec_deck_fly':'pec deck fly','dumbbell_shoulder_press':'dumbbell shoulder press',
    'lat_pulldown':'lat pulldown','pull_up':'pull up exercise',
    'bodyweight_glute_bridge':'glute bridge exercise',
    'high_knees':'high knees exercise',
    'bent_knee_sit_up':'sit up exercise',
    'barbell_overhead_press':'barbell overhead press exercise',
    'barbell_bent_over_row':'barbell bent over row exercise',
    'conventional_deadlift':'barbell deadlift exercise',
    'lat_pulldown_alternative':'lat pull down exercise',
    'hammer_curl_additional':'hammer dumbbell curl exercise demonstration',
    'cable_y_raise_additional':'cable Y raises shoulder exercise',
    'cable_chest_fly_additional':'standing cable fly chest exercise demo',
    'reverse_pec_deck_fly_additional':'rear delt fly machine exercise demonstration',
    'seated_cable_row_additional':'seated low cable row exercise demonstration',
    'dumbbell_chest_fly_additional':'flat dumbbell chest fly exercise demonstration',
    'machine_chest_press_additional':'seated chest press machine exercise demonstration',
    'reverse_lunge_additional':'bodyweight backward lunge exercise demonstration',
    'cable_y_raise_more':'standing cable Y raise exercise demonstration',
    'cable_y_raise_spanish':'elevaciones Y polea ejercicio',
    'cable_y_raise_french':'élévation Y poulie musculation',
    'cable_y_raise_alternate':'Y raise cables shoulder tutorial',
}


def search(query):
    # params来自原站searchFilterRenderer的Creative Commons选项，不推断隐藏接口。
    url='https://www.youtube.com/results?'+urllib.parse.urlencode({'search_query':query,'sp':'EgIwAQ%3D%3D'})
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'})
    with urllib.request.urlopen(request,timeout=30) as r:page=r.read(6*1024*1024).decode()
    match=re.search(r'var ytInitialData\s*=\s*',page)
    if not match:raise ValueError('公开搜索数据不可读')
    data,_=json.JSONDecoder().raw_decode(page[match.end():]);videos=[]
    def walk(value):
        if isinstance(value,dict):
            if 'videoRenderer' in value:
                r=value['videoRenderer']
                videos.append({'id':r.get('videoId'),'title':''.join(t.get('text','') for t in r.get('title',{}).get('runs',[])),
                               'author':''.join(t.get('text','') for t in r.get('ownerText',{}).get('runs',[])),
                               'duration':r.get('lengthText',{}).get('simpleText'),'license_status':'cc_search_candidate_not_yet_verified'})
            for v in value.values():walk(v)
        elif isinstance(value,list):
            for v in value:walk(v)
    walk(data)
    return list({r['id']:r for r in videos if r.get('id')}.values())[:10]


def run():
    path=ROOT/'data/datasets/development-movements/cc-candidates.json'
    data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'schema_version':1,'date':'2026-10-22','queries':{}}
    for action,query in QUERIES.items():
        if action in data['queries']:continue
        rows=search(query);data['queries'][action]={'query':query,'videos':rows}
        path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'action':action,'candidates':len(rows),'first_two':rows[:2]},ensure_ascii=False),flush=True)
        time.sleep(1)


if __name__=='__main__':run()
