"""认证/CSRF/不落正文；隔离数据库，合成身体资料。"""
import json,unittest
from sqlalchemy import select,func
import test_formal_records as shared
from fitness_app import db_schema as s

class NutritionRouteTests(unittest.TestCase):
    def setUp(self):self.fx=shared.RecordTests();self.fx.setUp()
    def tearDown(self):self.fx.tearDown()
    def test_auth_csrf_no_store_and_no_persistent_body(self):
        path='/api/v1/nutrition/estimate';payload={'inputs':{'age_years':25,'weight':'73.41927','weight_unit':'kg','exercise_status':'regular'},'input_version':4}
        anonymous=self.fx.app.test_client();self.assertEqual(anonymous.post(path,json=payload).status_code,401)
        self.assertEqual(self.fx.client.post(path,json=payload).status_code,403)
        r=self.fx.post(self.fx.client,path,payload);self.assertEqual(r.status_code,200,r.json)
        self.assertEqual(r.headers['Cache-Control'],'no-store');self.assertFalse(r.json['data']['provider_called'])
        self.assertEqual(r.json['data']['items'][0]['status'],'calculated')
        self.assertEqual(r.json['data']['items'][1]['status'],'input_required')
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.ai_requests)),0)
            self.assertEqual(c.scalar(select(func.count()).select_from(s.training_profiles)),0)
        self.assertNotIn(b'73.41927',self.fx.db.path.read_bytes())
        self.assertEqual(self.fx.client.get(path).status_code,405)
    def test_coach_context_status_is_authenticated_and_does_not_expose_body_fields(self):
        path='/api/v1/nutrition/coach-context'
        self.assertEqual(self.fx.app.test_client().get(path).status_code,401)
        self.fx.app.extensions['training_info'].write(self.fx.user,self.fx.auth,{'expected_version':0,'height_cm':175,'age_years':24,'weight_kg':80},s.new_id())
        self.fx.app.extensions['nutrition_profiles'].write(self.fx.user,self.fx.auth,{'expected_version':0,'inputs':{'subject':'self','age_years':24,'weight':70,'weight_unit':'kg','formula_sex':'male','activity_category':'light','exercise_status':'regular'}})
        response=self.fx.client.get(path);self.assertEqual(response.status_code,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
        state=response.json['data'];self.assertEqual(state['calculated_items'],2);self.assertTrue(state['has_training_profile']);self.assertTrue(state['has_nutrition_profile'])
        self.assertNotIn('inputs',state);self.assertNotIn('weight',json.dumps(state));self.assertEqual(state['training_version'],1)

    def test_unknown_and_invalid_inputs_do_not_echo_values(self):
        r=self.fx.post(self.fx.client,'/api/v1/nutrition/estimate',{'inputs':{'weight':'sensitive-fixture-value','weight_unit':'kg'}})
        self.assertEqual(r.status_code,422);self.assertNotIn('sensitive-fixture-value',json.dumps(r.json))
        r=self.fx.post(self.fx.client,'/api/v1/nutrition/estimate',{'inputs':{},'save':True})
        self.assertEqual(r.status_code,400)

if __name__=='__main__':unittest.main()
