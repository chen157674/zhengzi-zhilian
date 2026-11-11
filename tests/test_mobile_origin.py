"""手机HTTPS入口的Host/来源/转发信任与Cookie；不修改任何真实代理/证书。"""
import unittest
from flask import request,session,jsonify
from fitness_app import create_app
from fitness_app.mobile_origin import public_origin


class MobileOriginTests(unittest.TestCase):
    def setUp(self):
        self.app=create_app({'TESTING':True,'MOBILE_PUBLIC_ORIGIN':'https://fitness.example.test','SECRET_KEY':'fixture-only'})
        @self.app.route('/fixture',methods=['GET','POST'])
        def probe():session['fixture']=True;return jsonify(secure=request.is_secure,origin=request.host_url)
        self.client=self.app.test_client()
    def test_explicit_https_proxy_origin_sets_secure_cookie_and_keeps_local_access(self):
        for peer in ('127.0.0.1','::1','127.0.0.1:43120','[::1]:43120'):
            with self.subTest(peer=peer):
                r=self.client.get('/fixture',base_url='http://fitness.example.test',headers={'X-Forwarded-Proto':'https'},environ_overrides={'REMOTE_ADDR':peer})
                self.assertEqual(r.status_code,200,r.json);self.assertTrue(r.json['secure']);self.assertEqual(r.json['origin'],'https://fitness.example.test/')
                self.assertIn('Secure;',r.headers['Set-Cookie'])
        local=self.client.get('/fixture');self.assertEqual(local.status_code,200);self.assertFalse(local.json['secure'])
        waitress=self.client.get('/fixture',base_url='https://fitness.example.test',environ_overrides={'REMOTE_ADDR':'127.0.0.1'})
        self.assertEqual(waitress.status_code,200,waitress.json);self.assertTrue(waitress.json['secure'])
    def test_forwarding_is_not_accepted_from_other_peers_or_for_local_admin_stop(self):
        for peer,proto,path in [('192.168.1.7','https','/fixture'),('127.0.0.1.evil:443','https','/fixture'),
                                ('127.0.0.1:bad','https','/fixture'),('[::1]evil:443','https','/fixture'),
                                ('127.0.0.1','http','/fixture'),('127.0.0.1','https,http','/fixture'),
                                ('127.0.0.1','https','/_local/stop')]:
            r=self.client.get(path,base_url='http://fitness.example.test',headers={'X-Forwarded-Proto':proto},environ_overrides={'REMOTE_ADDR':peer})
            self.assertEqual(r.status_code,403,(peer,proto,path,r.json))
        r=self.client.get('/fixture',base_url='http://localhost',headers={'X-Forwarded-Host':'fitness.example.test','X-Forwarded-Proto':'https'})
        self.assertFalse(r.json['secure'])
    def test_foreign_host_and_cross_origin_writes_stay_rejected(self):
        self.assertEqual(self.client.get('/fixture',base_url='http://evil.example').status_code,400)
        r=self.client.post('/fixture',base_url='http://fitness.example.test',headers={'X-Forwarded-Proto':'https','Origin':'https://evil.example'})
        self.assertEqual(r.status_code,403,r.json)
    def test_configuration_rejects_non_https_wildcards_paths_and_loopback(self):
        for value in ['http://fitness.example.test','https://*.example.test','https://name:secret@example.test','https://example.test/path','https://example.test?x=1']:
            with self.assertRaises(ValueError):public_origin(value)
        with self.assertRaises(ValueError):create_app({'TESTING':True,'MOBILE_PUBLIC_ORIGIN':'https://localhost'})
        self.assertEqual(public_origin('https://fitness.example.test:443/'),'https://fitness.example.test')


if __name__=='__main__':unittest.main()
