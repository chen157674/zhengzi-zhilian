"""本次主动输入的确定性参考范围；无网络、无资料持久化。"""
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_CEILING

PROTEIN_METHOD='protein_issn_2017_v1'
ENERGY_METHOD='energy_fao2004_schofield_pal_v1'
FIELDS={'subject','age_years','adult','weight','weight_unit','height','height_unit',
        'formula_sex','activity_category','exercise_status','goal','special_context'}
PAL={'light':('1.40','1.69'),'moderate':('1.70','1.99'),'vigorous':('2.00','2.40')}
SOURCES={
 'protein':{'id':'S09','title':'ISSN：蛋白质与运动（2017）','url':'https://pmc.ncbi.nlm.nih.gov/articles/PMC5477153/'},
 'energy':{'id':'S10','title':'FAO/WHO/UNU：Human energy requirements，第5章（2004）','url':'https://www.fao.org/4/y5686e/y5686e07.htm'},
}

class NutritionInputError(ValueError):
    def __init__(self,field,code='NUTRITION_INPUT_INVALID'):
        super().__init__('请检查输入字段及单位');self.field=field;self.code=code

def number(value,field,maximum):
    if isinstance(value,bool) or not isinstance(value,(int,float,str,Decimal)):raise NutritionInputError(field)
    if isinstance(value,str) and (len(value)>32 or not value.strip()):raise NutritionInputError(field)
    try:result=Decimal(str(value))
    except InvalidOperation:raise NutritionInputError(field) from None
    if not result.is_finite() or result<=0 or result>maximum:raise NutritionInputError(field)
    return result

def normalize_inputs(data):
    if not isinstance(data,dict) or set(data)-FIELDS:raise NutritionInputError('inputs')
    result={}
    for field,allowed in {
      'subject':{'self','other','hypothetical'},'formula_sex':{'male','female'},
      'activity_category':set(PAL),'exercise_status':{'regular','none','unknown'},
      'goal':{'maintenance','muscle_gain','general'},
      'special_context':{'none_declared','not_applicable','unknown'},
    }.items():
        value=data.get(field)
        if value is not None:
            if not isinstance(value,str) or value not in allowed:raise NutritionInputError(field)
            result[field]=value
    if data.get('adult') is not None:
        if type(data['adult']) is not bool:raise NutritionInputError('adult')
        result['adult']=data['adult']
    if data.get('age_years') is not None:
        age=number(data['age_years'],'age_years',120)
        if age!=age.to_integral_value():raise NutritionInputError('age_years')
        result['age_years']=int(age)
        if 'adult' in result and result['adult']!=(age>=18):raise NutritionInputError('adult','NUTRITION_INPUT_CONFLICT')
    for name,units,cap in [('weight',{'kg':Decimal(1),'jin':Decimal('.5'),'lb':Decimal('.45359237')},500),
                           ('height',{'cm':Decimal(1),'m':Decimal(100)},300)]:
        value=data.get(name);unit=data.get(name+'_unit')
        if value is None:
            if unit is not None:raise NutritionInputError(name)
            continue
        if not isinstance(unit,str) or unit not in units:raise NutritionInputError(name+'_unit','NUTRITION_UNIT_AMBIGUOUS')
        normalized=number(value,name,Decimal(cap)/units[unit])*units[unit]
        result[name+('_kg' if name=='weight' else '_cm')]=format(normalized.normalize(),'f')
    return result

def display(value,step,upper=False):
    return int((value/step).to_integral_value(rounding=ROUND_CEILING if upper else ROUND_FLOOR)*step)

def estimate(data,input_version=0,items=None):
    if type(input_version) is not int or not 0<=input_version<=2**31-1:raise NutritionInputError('input_version')
    if items is None:items=['protein','energy']
    if not isinstance(items,list) or not items or len(items)>2 or any(not isinstance(x,str) or x not in SOURCES for x in items) or len(set(items))!=len(items):raise NutritionInputError('items')
    inputs=normalize_inputs(data)
    adult=inputs.get('age_years',0)>=18 if 'age_years' in inputs else inputs.get('adult')
    applicable=adult is not False and inputs.get('special_context')!='not_applicable'
    result=[]
    for item in items:
        card={'item':item,'status':'input_required','unit':'g/day' if item=='protein' else 'kcal/day',
              'input_version':input_version,'method_id':PROTEIN_METHOD if item=='protein' else ENERGY_METHOD,
              'source':dict(SOURCES[item]),'missing':[],'storage_mode':'ephemeral'}
        if not applicable:card.update(status='method_not_applicable',message='当前自述不适用本版成人参考方法，可继续查看一般知识。')
        else:
            missing=[]
            if adult is None:missing.append('adult')
            if 'weight_kg' not in inputs:missing.append('weight')
            if item=='protein':
                if inputs.get('exercise_status')!='regular':missing.append('exercise_status')
                if inputs.get('exercise_status')=='none':card.update(status='method_not_applicable',message='此范围用于规律运动者，当前不套用该系数。')
            else:missing.extend(k for k in ('age_years','formula_sex','activity_category') if k not in inputs)
            card['missing']=list(dict.fromkeys(missing))
            if not missing:
                weight=Decimal(inputs['weight_kg'])
                if item=='protein':
                    low,high=weight*Decimal('1.4'),weight*Decimal('2.0');step=Decimal(5)
                    card.update(coefficients=['1.4','2.0'],message='健康成年规律运动者的每日蛋白质参考范围。')
                else:
                    age=inputs['age_years'];band=0 if age<30 else 1 if age<60 else 2
                    coefficients={'male':[('15.057','692.2'),('11.472','873.1'),('11.711','587.7')],
                                  'female':[('14.818','486.6'),('8.126','845.6'),('9.082','658.5')]}
                    slope,offset=map(Decimal,coefficients[inputs['formula_sex']][band])
                    bmr=slope*weight+offset;pal=list(map(Decimal,PAL[inputs['activity_category']]))
                    low,high=bmr*pal[0],bmr*pal[1];step=Decimal(50)
                    card.update(bmr_kcal=str(bmr),bmr_coefficients=[str(slope),str(offset)],pal=[str(x) for x in pal],
                                message='维持体重的每日能量参考范围；不含自动增肌盈余。')
                card.update(status='calculated',lower=str(low),upper=str(high),display_lower=display(low,step),
                            display_upper=display(high,step,True),display_rounding={'step':int(step),'direction':'outward'})
        result.append(card)
    return {'input_version':input_version,'inputs':inputs,'items':result,'storage_mode':'ephemeral',
            'not_actual_intake':True,'provider_called':False}
