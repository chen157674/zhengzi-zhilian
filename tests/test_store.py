import sys
from pathlib import Path
import tempfile
import threading
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from server import Store,validate_snapshot,make_server

def sample(revision=1,state='running'):
    return dict(revision=revision,state=state,hold=0,observed=2,duration=3,
                reps=[dict(index=1,start=0,end=2,issue=None)],intervals=[])

class StoreTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='store-',dir=ROOT/'.tmp')
        self.path=Path(self.temp.name)/'sessions.sqlite3'
        self.store=Store(self.path)
    def tearDown(self):self.temp.cleanup()
    def test_idempotence_and_stale_writes(self):
        ident=self.store.create('squat','video')['id']
        self.store.update(ident,sample(2))
        self.store.update(ident,sample(1))
        self.store.update(ident,sample(2))
        row=self.store.list()[0]
        self.assertEqual(row['revision'],2)
        self.assertEqual(len(row['reps']),1)
    def test_terminal_session_cannot_reopen(self):
        ident=self.store.create('squat','demo')['id']
        self.store.update(ident,sample(1,'ended'))
        with self.assertRaises(ValueError):self.store.update(ident,sample(2))
    def test_restart_preserves_summary_and_marks_interruption(self):
        ident=self.store.create('squat','camera')['id']
        self.store.update(ident,sample())
        row=Store(self.path).list()[0]
        self.assertEqual(row['state'],'interrupted');self.assertEqual(len(row['reps']),1)
    def test_invalid_data_is_rejected(self):
        with self.assertRaises(ValueError):self.store.create('unknown','camera')
        for patch in [{'hold':float('nan')},{'duration':-1},{'revision':True},{'state':'success'}, {'reps':[{'index':1,'start':3,'end':2}]}]:
            with self.assertRaises(ValueError):validate_snapshot({**sample(),**patch})
    def test_http_boundary_and_write_validation(self):
        server=make_server(0,self.path)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            for headers in [{'Host':'example.invalid'},{'Origin':'https://example.invalid'}]:
                with self.assertRaises(HTTPError) as result:urlopen(Request(base+'/api/sessions',headers=headers))
                self.assertEqual(result.exception.code,403)
                result.exception.close()
            req=Request(base+'/api/sessions',data=b'{"action":"unknown","source":"camera"}',headers={'Content-Type':'application/json'})
            with self.assertRaises(HTTPError) as result:urlopen(req)
            self.assertEqual(result.exception.code,400)
            result.exception.close()
            with urlopen(base+'/api/sessions') as response:self.assertEqual(json.load(response),[])
        finally:
            server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
