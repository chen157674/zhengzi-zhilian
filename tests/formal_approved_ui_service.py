"""新稿浏览器验收：真实路由/鉴权/任务/账本，供应商仅为受控协议替身。"""
import json
import os
import sys
import threading
from pathlib import Path
from flask import jsonify,request
from fitness_app.paths import ROOT,configure_environment

configure_environment()
sys.path.insert(0,str(ROOT/'tests'))
from fitness_app import app as app_module
from fitness_app.runtime import serve
from fitness_app.meal_recommendations import VERSION
from fitness_app.diet_journal import identify_foods
from test_training_plan_jobs import PlanProvider
from test_training_reviews import ReviewProvider
from test_ai_jobs import Provider as ReportProvider

assert Path(os.environ['FITNESS_DATABASE_PATH']).parent.resolve()==ROOT/'.tmp/tests/approved-ui'
release=threading.Event();release.set()
counts={'coach':0,'plan':0,'review':0,'report':0}


class CoachProvider:
    def tool_completion(self,key,model,messages,tools,**kwargs):
        counts['coach']+=1
        if not release.wait(4):raise RuntimeError('isolated fixture wait expired')
        sources=[]
        for item in messages:
            value=item.get('content') or ''
            if value.startswith('服务器本次实际检索结果'):
                sources=json.loads(value[value.index('['):])
        raw={'summary':'先站稳，再按图解缓慢练习，保持动作可控。',
             'knowledge_ids':[sources[0]['id']] if sources else [],
             'evidence_ids':[],'focus_evidence_id':None,'question':None}
        system=messages[0]['content']
        if '你正在执行真实饮食记录分析' in system:
            original=next(item['content'].split('原始记录：',1)[1]
                          for item in messages if (item.get('content') or '').startswith('待分析的'))
            foods=[{**food,'evidence':original,'amount':None} for food in identify_foods(original)]
            raw.update(summary='下一餐把主食、鱼肉蛋豆和蔬菜搭在一起。',
                       analysis={'foods':foods,'next_meal':'米饭、豆腐和青菜一起搭配。','swaps':[]})
        elif 'meal_suggestion' in system:
            raw.update(summary='可以把米饭、豆腐和青菜搭成这一餐。',
                       meal_suggestion={'content_version':VERSION,'meal_type':'lunch','scene':'home',
                                        'slots':{'staple':'rice','protein':'tofu','vegetable':'leafy-greens'}})
        return {'message':{'role':'assistant','content':json.dumps(raw,ensure_ascii=False)},
                'calls':[],'usage':{'prompt_tokens':30,'completion_tokens':30,'total_tokens':60},
                'estimated_micro_cny':100}


class TrainingProvider:
    def tool_completion(self,*args,**kwargs):
        messages=args[2];data=json.loads(messages[-1]['content'])
        name='plan' if 'conditions' in data else 'review';counts[name]+=1
        if not release.wait(4):raise RuntimeError('isolated fixture wait expired')
        return (PlanProvider() if name=='plan' else ReviewProvider()).tool_completion(*args,**kwargs)


class Reports(ReportProvider):
    def generate(self,*args,**kwargs):
        counts['report']+=1
        if not release.wait(4):raise RuntimeError('isolated fixture wait expired')
        return super().generate(*args,**kwargs)


original=app_module.create_app


def create(config):
    app=original({**config,'TESTING':True,'DATABASE_PATH':os.environ['FITNESS_DATABASE_PATH']})
    app.extensions['coach_provider_factory']=CoachProvider
    app.extensions['training_provider_factory']=TrainingProvider
    app.extensions['ai_provider_factory']=Reports

    @app.get('/api/v1/_fixture/approved/stats')
    def stats():
        return jsonify(data={'calls':counts,'paid_provider':False})

    @app.post('/api/v1/_fixture/approved/control')
    def control():
        if request.get_json().get('hold'):release.clear()
        else:release.set()
        return jsonify(data={'configured':True})

    return app


app_module.create_app=create
serve(0)
