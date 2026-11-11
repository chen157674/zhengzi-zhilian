"""来源公式、单位与不完整输入边界；仅合成资料。"""
import unittest
from decimal import Decimal
from fitness_app.nutrition_estimates import estimate,normalize_inputs,NutritionInputError

class NutritionTests(unittest.TestCase):
    def values(self,**extra):return {'age_years':25,'weight':70,'weight_unit':'kg','formula_sex':'male',
        'activity_category':'light','exercise_status':'regular',**extra}
    def test_registered_examples_and_rounding(self):
        result=estimate(self.values(),7);p,e=result['items']
        self.assertEqual((Decimal(p['lower']),Decimal(p['upper'])),(Decimal(98),Decimal(140)))
        self.assertEqual((p['display_lower'],p['display_upper']),(95,140))
        self.assertEqual(Decimal(e['bmr_kcal']),Decimal('1746.19'))
        self.assertEqual(Decimal(e['upper']),Decimal('2951.0611'))
        self.assertEqual((e['display_lower'],e['display_upper']),(2400,3000))
        self.assertEqual(result['input_version'],7);self.assertFalse(result['provider_called'])
    def test_units_match_and_height_is_optional(self):
        a=estimate(self.values());b=estimate(self.values(weight=140,weight_unit='jin',height='1.75',height_unit='m'))
        self.assertEqual(a['items'],b['items']);self.assertEqual(b['inputs']['height_cm'],'175')
        self.assertEqual(Decimal(normalize_inputs({'weight':1,'weight_unit':'lb'})['weight_kg']),Decimal('.45359237'))
    def test_age_branches_at_18_30_60(self):
        for age,sex,w,bmr in [(18,'male',70,'1746.19'),(30,'male',70,'1676.14'),(60,'female',60,'1203.420')]:
            e=estimate(self.values(age_years=age,formula_sex=sex,weight=w))['items'][1]
            self.assertEqual(Decimal(e['bmr_kcal']),Decimal(bmr))
    def test_partial_and_no_guess(self):
        result=estimate({'adult':True,'weight':70,'weight_unit':'kg','exercise_status':'regular'})
        self.assertEqual(result['items'][0]['status'],'calculated')
        self.assertEqual(result['items'][1]['status'],'input_required')
        self.assertEqual(set(result['items'][1]['missing']),{'age_years','formula_sex','activity_category'})
        self.assertEqual(estimate({'weight':70,'weight_unit':'kg'})['items'][0]['status'],'input_required')
    def test_not_applicable_is_not_normal(self):
        for data in [self.values(age_years=17),self.values(special_context='not_applicable')]:
            self.assertTrue(all(x['status']=='method_not_applicable' for x in estimate(data)['items']))
        self.assertEqual(estimate(self.values(exercise_status='none'))['items'][0]['status'],'method_not_applicable')
    def test_invalid_values_conflicts_and_unknown_methods(self):
        invalid=[{'weight':True,'weight_unit':'kg'},{'weight':float('nan'),'weight_unit':'kg'},
            {'weight':'Infinity','weight_unit':'kg'},{'weight':-1,'weight_unit':'kg'},
            {'weight':501,'weight_unit':'kg'},{'weight':70},{'age_years':17,'adult':True},
            {'formula_sex':[]},{'activity_category':'weekly_three'},{'formula':'whatever'},
            {'age_years':30.5},{'height':3.1,'height_unit':'m'}]
        for data in invalid:
            with self.subTest(data=data),self.assertRaises(NutritionInputError):estimate(data)
        for items in [[{}],['protein','protein'],['unknown']]:
            with self.assertRaises(NutritionInputError):estimate({},items=items)
    def test_muscle_goal_does_not_add_energy(self):
        self.assertEqual(estimate(self.values(goal='muscle_gain'))['items'],estimate(self.values(goal='maintenance'))['items'])

if __name__=='__main__':unittest.main()
