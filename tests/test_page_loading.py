"""缓存版本、模块依赖与教学发布边界；测试文件只写项目隔离目录。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

from fitness_app.knowledge_store import KnowledgeStore
from fitness_app.learning_guides import learning_cards, tutorial_for
from fitness_app.paths import ROOT
from fitness_app.web_assets import register_web_assets


class WebAssetTests(unittest.TestCase):
    def test_cached_modules_keep_their_content_and_dependencies_on_new_release(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp/tests') as temporary:
            directory=Path(temporary)
            (directory/'app.js').write_text("import './child.js';",encoding='utf-8')
            (directory/'child.js').write_text('export const value=1;',encoding='utf-8')
            (directory/'style.css').write_text('body{color:red}',encoding='utf-8')
            app=Flask(__name__,static_folder=str(directory))
            register_web_assets(app)
            version=app.extensions['web_assets']['version']
            url=f'/static/bundle/{version}/child.js'
            client=app.test_client()
            response=client.get(url)
            self.assertEqual(response.status_code,200)
            self.assertIn('max-age=31536000',response.headers['Cache-Control'])
            self.assertIn('immutable',response.headers['Cache-Control'])
            self.assertEqual(client.get(url,headers={'If-None-Match':response.headers['ETag']}).status_code,304)
            self.assertEqual(client.get(f'/static/bundle/{version}/app.js').data,b"import './child.js';")
            self.assertEqual(client.get(f'/static/bundle/{version}/style.css').status_code,200)
            self.assertEqual(client.get(f'/static/bundle/{version}/../unregistered.js').status_code,404)
            (directory/'child.js').write_text('export const value=2;',encoding='utf-8')
            # 正在运行的旧版本持有原资源快照，不能在旧 URL 下偷偷返回新内容。
            self.assertEqual(client.get(url).data,b'export const value=1;')
            next_app=Flask(__name__,static_folder=str(directory))
            register_web_assets(next_app)
            next_version=next_app.extensions['web_assets']['version']
            self.assertNotEqual(next_version,version)
            self.assertEqual(next_app.test_client().get(url).status_code,404)
            self.assertEqual(next_app.test_client().get(f'/static/bundle/{next_version}/child.js').data,b'export const value=2;')


class LearningLoadingTests(unittest.TestCase):
    def setUp(self):
        self.store=KnowledgeStore(index_dir='.tmp/nonexistent-knowledge-index')

    def test_catalogue_builds_action_capabilities_once_and_still_honors_withdrawal(self):
        from fitness_app.catalogue import definitions
        with patch('fitness_app.catalogue.definitions',wraps=definitions) as build:
            catalog=self.store.catalog()
            self.assertEqual(len(catalog['lessons']),33)
            self.assertEqual(build.call_count,1)
        row=self.store.by_id['k:lesson:lat_pulldown']
        row['status']='withdrawn'
        self.assertNotIn('lat_pulldown',[x['action_id'] for x in learning_cards(self.store)])
        self.assertIsNone(tutorial_for(self.store,'lat_pulldown'))
        row['status']='published'
        self.assertIn('lat_pulldown',[x['action_id'] for x in learning_cards(self.store)])

    def test_changed_teaching_image_is_not_accepted_by_a_warm_hash_cache(self):
        import hashlib
        import os
        from fitness_app.learning_guides import _verified_image
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp/tests') as temporary:
            path=Path(temporary)/'fixture.webp'
            original=b'isolated-image-A'
            path.write_bytes(original)
            digest=hashlib.sha256(original).hexdigest()
            def verified():
                stat=path.stat()
                return _verified_image(str(path),stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size,len(original),digest)
            self.assertTrue(verified())
            self.assertTrue(verified())
            previous=path.stat().st_mtime_ns
            path.write_bytes(b'changed-image-B')
            os.utime(path,ns=(previous+1000000,previous+1000000))
            self.assertFalse(verified())


if __name__=='__main__':
    unittest.main()
