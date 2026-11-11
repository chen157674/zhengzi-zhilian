"""动作配置缓存必须隔离调用方修改，并保留实际文件变化的重新读取。"""
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
from fitness_app.paths import ROOT
from fitness_app.upstream import load_upstream_definition,_read_definition


class DefinitionCacheTests(unittest.TestCase):
    def test_each_reader_gets_independent_nested_configuration(self):
        first=load_upstream_definition('squat');expected=first['name']
        first['name']='changed';first['injected']={'nested':True}
        second=load_upstream_definition('squat')
        self.assertEqual(second['name'],expected);self.assertNotIn('injected',second)

    def test_changed_file_state_reloads_and_invalid_configuration_is_not_cached(self):
        with TemporaryDirectory(dir=ROOT/'.tmp/tests',prefix='definition-cache-') as folder:
            path=Path(folder)/'sample.yaml';path.write_text('name: before\nitems: [one]\n',encoding='utf-8')
            with patch('fitness_app.upstream.DEFINITIONS',Path(folder)):
                first=load_upstream_definition('sample');first['items'].append('changed')
                self.assertEqual(load_upstream_definition('sample')['items'],['one'])
                path.write_text('name: after-longer\nitems: [two]\n',encoding='utf-8')
                self.assertEqual(load_upstream_definition('sample')['name'],'after-longer')
                path.write_text('items: [invalid]\n',encoding='utf-8')
                with self.assertRaises(ValueError):load_upstream_definition('sample')
        _read_definition.cache_clear()


if __name__=='__main__':unittest.main()
