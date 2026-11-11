import unittest
from fitness_app.control import ControlRegistry
from fitness_app.auth import APIError
from fitness_app.database import StorageError,ConflictError
from fitness_app.db_schema import new_id


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.mono=[0.0];self.wall=[1000];self.registry=ControlRegistry(lambda:self.mono[0],lambda:self.wall[0])
        self.uid,self.auth,self.sid,self.page=new_id(),new_id(),new_id(),new_id();self.commits=0

    def commit(self):self.commits+=1;return {'state_version':self.commits,'status':'paused'}
    def claim(self,page=None,takeover=False,key=None,callback=None):
        return self.registry.claim(self.uid,self.auth,100000,self.sid,page or self.page,takeover,0,key or new_id(),callback or self.commit)
    def verify(self,issued,**overrides):
        values=dict(user_id=self.uid,auth_id=self.auth,session_id=self.sid,token=issued['control_token'],controller_epoch=issued['controller_epoch'],stream_epoch=issued['stream_epoch']);values.update(overrides)
        return self.registry.verify(**values)

    def test_secret_once_receipt_digest_and_read_only_snapshot(self):
        self.assertFalse(self.registry.snapshot(self.uid,self.sid)['has_controller']);self.assertEqual(self.commits,0)
        key=new_id();issued=self.claim(key=key);receipt=self.claim(key=key)
        self.assertTrue(issued['credential_available']);self.assertFalse(receipt['credential_available']);self.assertNotIn('control_token',receipt);self.assertEqual(self.commits,1)
        with self.assertRaises(ConflictError):self.claim(page=new_id(),key=key)
        self.assertNotIn(issued['control_token'],str(self.registry.entries));self.assertNotIn(issued['control_token'],str(self.registry.receipts))
        self.assertNotIn('token_hash',self.verify(issued));self.assertFalse(self.registry.snapshot(self.uid,self.sid,self.page)['controlled_elsewhere'])

    def test_takeover_explicit_failed_transaction_keeps_old_and_success_revokes(self):
        issued=self.claim()
        with self.assertRaises(APIError):self.claim(page=new_id())
        def failed():raise StorageError('controlled commit failure')
        with self.assertRaises(StorageError):self.claim(page=new_id(),takeover=True,callback=failed)
        self.verify(issued);next_control=self.claim(page=new_id(),takeover=True)
        self.assertGreater(next_control['controller_epoch'],issued['controller_epoch'])
        with self.assertRaises(APIError):self.verify(issued)
        self.verify(next_control)
        with self.assertRaises(APIError):self.registry.cleanup_binding(self.auth,self.sid,issued['control_token'],issued['controller_epoch'],issued['stream_epoch'])

    def test_get_no_renew_heartbeat_does_not_extend_auth_and_cleanup_scope(self):
        issued=self.claim();self.mono[0]=14;self.registry.snapshot(self.uid,self.sid);self.mono[0]=15
        self.assertFalse(self.registry.snapshot(self.uid,self.sid)['has_controller']);self.assertEqual(self.registry.expire()[0]['reason'],'lease_expired')
        with self.assertRaises(APIError):self.verify(issued)
        binding=self.registry.cleanup_binding(self.auth,self.sid,issued['control_token'],issued['controller_epoch'],issued['stream_epoch'])
        self.assertEqual(set(binding),{'user_id','auth_id','session_id'})
        issued=self.claim();self.wall[0]=99999
        reply=self.registry.heartbeat(self.uid,self.auth,self.sid,issued['control_token'],issued['controller_epoch'],issued['stream_epoch']);self.assertEqual(reply['lease_remaining_ms'],1)
        self.wall[0]=100000
        with self.assertRaises(APIError):self.verify(issued)
        self.assertEqual(self.registry.expire()[0]['reason'],'auth_expired')

    def test_rotate_stream_foreign_auth_and_revoke_old_flow(self):
        issued=self.claim()
        for overrides in [{'user_id':new_id()},{'auth_id':new_id()},{'controller_epoch':True}]:
            with self.assertRaises(APIError):self.verify(issued,**overrides)
        epoch=self.registry.rotate_stream(self.uid,self.auth,self.sid,issued['control_token'],issued['controller_epoch'],issued['stream_epoch'])
        with self.assertRaises(APIError):self.verify(issued)
        issued['stream_epoch']=epoch;self.verify(issued)
        self.assertEqual(len(self.registry.revoke(self.uid,self.auth)),1)
        with self.assertRaises(APIError):self.verify(issued)
        with self.assertRaises(APIError):self.registry.cleanup_binding(new_id(),self.sid,issued['control_token'],issued['controller_epoch'],epoch)
        self.registry.forget(self.sid)
        with self.assertRaises(APIError):self.registry.cleanup_binding(self.auth,self.sid,issued['control_token'],issued['controller_epoch'],epoch)


if __name__=='__main__':unittest.main()
