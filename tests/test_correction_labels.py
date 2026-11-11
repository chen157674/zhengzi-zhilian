"""纠错试标权限、未知语义、原片边界、幂等和可追溯修改。"""
import unittest
from unittest.mock import patch
from pathlib import Path
from fitness_app import db_schema as s
import test_formal_records as shared


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.config_overrides={'CORRECTION_LABELS_ENABLED':True};self.fx.setUp()
        self.admin=self.fx.app.test_client();self.fx.login(self.admin,'Record_Admin')
        self.url='/api/v1/me/correction-labels'

    def tearDown(self):self.fx.tearDown()

    def payload(self,**values):
        return {'id':s.new_id(),'expected_version':0,'demo_id':'user-1694','start_ms':0,'end_ms':2000,
                'error_code':'E01','verdict':'present','side':'left','phase':'work','note':'试标，不是独立教练真值',**values}

    def post(self,data):return self.fx.post(self.admin,self.url,data)

    def test_owner_only_and_read_does_not_create_annotation_files(self):
        self.assertEqual(self.fx.client.get(self.url).status_code,200)
        self.assertEqual(self.fx.client.get('/account/correction-labels').status_code,404)
        self.assertEqual(self.fx.app.test_client().get(self.url).status_code,401)
        self.assertEqual(self.fx.client.get('/admin/correction-labels').status_code,403)
        result=self.admin.get(self.url);self.assertEqual(result.status_code,200,result.json)
        self.assertEqual(result.json['data']['records'],[])
        self.assertEqual(len(result.json['data']['errors']),16)
        self.assertFalse(self.fx.app.extensions['correction_labels'].root.exists())

    def test_release_default_has_no_label_routes_or_menu(self):
        from fitness_app import create_app
        with patch.dict('os.environ',{'FITNESS_ENABLE_CORRECTION_LABELS':'0'}):
            disabled=create_app({'TESTING':True,'DATABASE_PATH':str(self.fx.db.path),'SECRET_KEY':'test-only'})
        try:
            self.assertFalse(disabled.config['CORRECTION_LABELS_ENABLED'])
            self.assertNotIn('correction_labels',disabled.extensions)
            self.assertFalse(any('correction-labels' in rule.rule for rule in disabled.url_map.iter_rules()))
            client=disabled.test_client();self.fx.login(client,'Record_User')
            self.assertNotIn(b'/account/correction-labels',client.get('/settings').data)
            self.assertEqual(client.get('/account/correction-labels').status_code,404)
        finally:disabled.extensions['database'].dispose()

    def test_save_retry_and_revision_preserve_old_labels(self):
        payload=self.payload();first=self.post(payload)
        self.assertEqual(first.status_code,200,first.json)
        data=first.json['data'];self.assertEqual(data['version'],1)
        row=data['records'][0];self.assertEqual(row['present_error_codes'],['E01'])
        self.assertEqual(row['annotation_source'],'user_confirmed')
        self.assertFalse(row['independent_professional_review'])
        self.assertEqual(self.fx.client.get(self.url).json['data']['records'],[])
        again=self.post(payload);self.assertEqual(again.json['data']['version'],1)
        edited=self.post({**payload,'expected_version':1,'verdict':'absent','note':'修改为该项未见问题'})
        self.assertEqual(edited.status_code,200,edited.json)
        row=edited.json['data']['records'][0]
        self.assertEqual(row['revision'],2);self.assertEqual(row['present_error_codes'],[])
        self.assertEqual(row['history'][0]['present_error_codes'],['E01'])
        stale=self.post({**payload,'expected_version':1,'note':'过期修改'})
        self.assertEqual(stale.status_code,409,stale.json)

    def test_unknown_is_not_a_normal_label_and_bad_ranges_are_rejected(self):
        result=self.post(self.payload(verdict='unknown',error_code='E04'))
        self.assertEqual(result.status_code,200,result.json)
        row=result.json['data']['records'][0]
        self.assertEqual(row['assessed_error_codes'],[])
        self.assertEqual(row['unknown_error_codes'],['E04'])
        for changes in ({'start_ms':2000,'end_ms':1000},{'end_ms':1700000},{'error_code':[]},{'error_code':'E15'},{'demo_id':'../../private'}):
            result=self.post(self.payload(expected_version=1,**changes))
            self.assertEqual(result.status_code,422,result.json)


if __name__=='__main__':unittest.main()
