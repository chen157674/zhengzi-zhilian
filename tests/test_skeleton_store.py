"""可选轨迹生命周期/有限分块/权限；只检验合成骨架工程协议。"""
import copy,hashlib,sqlite3,unittest
from contextlib import closing
from unittest.mock import patch
from sqlalchemy import select,func
from fitness_app import db_schema as s
from fitness_app.catalogue import get_action
from fitness_app.database import StorageError,ConflictError
from fitness_app.skeleton_store import SkeletonStore,SkeletonBuffer,pack_frame,RETENTION_MS
import test_formal_records as fixtures


def frame(stamp=0,seq=0):
    return {'captured_ms':stamp,'frame_seq':seq,'width':640,'height':480,'model_version':'synthetic-pose',
        'framing':{'person_count':1,'usable':True,'landmarks':[{'x':.4+.1*(i%2),'y':.1+.02*i,'z':99.,'visibility':.99,'presence':.99,'valid':True} for i in range(33)]}}


class SkeletonStoreTests(unittest.TestCase):
    def setUp(self):self.fx=fixtures.RecordTests();self.fx.setUp();self.store=SkeletonStore(self.fx.store)
    def tearDown(self):self.fx.app.extensions['preview_manager'].stop();self.fx.tearDown()
    def make(self,enabled=True):
        return self.fx.store.create(self.fx.user,self.fx.auth,{'action_id':'barbell_overhead_press','capability_version':get_action('barbell_overhead_press')['capability_version'],
            'source':'camera','mode':'free','target':None,'save_skeleton':enabled},s.new_id())['session_id']
    def batch(self,start=0,n=20):return [pack_frame(frame((i+start)*100,i+start)) for i in range(n)]

    def test_default_off_has_no_tracks_and_invalid_or_multi_person_points_are_not_reconstructed(self):
        sid=self.make(False);self.assertEqual(self.store.append(self.fx.user,sid,0,self.batch(),0),'not_saved')
        self.assertEqual(self.store.read(self.fx.user,sid)['status'],'not_saved')
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.skeleton_tracks)),0)
        f=frame();f['framing']['landmarks'][11]['x']=float('nan');packed=pack_frame(f)
        self.assertNotIn(11,[v[0] for v in packed['j']]);self.assertEqual(len(packed['j'][0]),5);self.assertNotIn('z',str(packed['j']))
        f['framing']['person_count']=2;self.assertEqual(pack_frame(f)['j'],[])
        f=frame();f['identity_accepted']=False;self.assertEqual(pack_frame(f)['j'],[])

    def test_bounded_sampling_buffer_bind_pagination_and_coordinate_metadata(self):
        sid=self.make();buffer=SkeletonBuffer(self.store,self.fx.user,offset=1000)
        for i in range(61):buffer.push(frame(1000+i*50,i))
        self.assertEqual(len(buffer.frames),31);buffer.bind(sid);buffer.close()
        value=self.store.read(self.fx.user,sid,-1,8);self.assertEqual(value['status'],'complete');self.assertEqual(value['frame_count'],31)
        self.assertEqual([v['t'] for v in value['frames']],list(range(0,800,100)));self.assertEqual(value['next_after_ms'],700)
        self.assertEqual(value['metadata']['source_offset_ms'],1000);self.assertFalse(value['metadata']['mirrored']);self.assertTrue(value['metadata']['no_rgb_or_audio_saved'])
        self.assertEqual(value['expires_at_ms']-value['created_at_ms'],RETENTION_MS)
        self.assertEqual(self.store.read(self.fx.user,sid,700,8)['frames'][0]['t'],800)

    def test_idempotent_chunk_and_after_commit_confirmation_do_not_duplicate_frames(self):
        sid=self.make();batch=self.batch();self.store.append(self.fx.user,sid,0,batch,0);self.store.append(self.fx.user,sid,0,batch,0)
        changed=copy.deepcopy(batch);changed[0]['j']=[]
        with self.assertRaises(ConflictError):self.store.append(self.fx.user,sid,0,changed,0)
        self.assertEqual(self.store.summary(self.fx.user,sid)['frame_count'],20)
        other=self.make();failed=[False]
        def fault(point):
            if point=='after_commit' and not failed[0]:failed[0]=True;raise OSError('synthetic uncertain commit')
        buffer=SkeletonBuffer(SkeletonStore(self.fx.store,fault),self.fx.user,other)
        for i in range(20):buffer.push(frame(i*100,i))
        buffer.close();self.assertEqual(self.store.summary(self.fx.user,other)['frame_count'],20)

    def test_limits_preserve_old_chunks_and_do_not_prevent_training_summary(self):
        sid=self.make();self.store.append(self.fx.user,sid,0,self.batch(),0)
        with patch('fitness_app.skeleton_store.MAX_FRAMES',25):self.assertEqual(self.store.append(self.fx.user,sid,1,self.batch(20,6),0),'limited')
        self.assertEqual(self.store.read(self.fx.user,sid)['frame_count'],20)
        self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());self.fx.finish(sid)
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['status'],'ended_early')
        other=self.make()
        with patch('fitness_app.skeleton_store.MAX_USER_BYTES',1):self.assertEqual(self.store.append(self.fx.user,other,0,self.batch(),0),'limited')

    def test_owner_delete_is_versioned_idempotent_and_active_writer_cannot_restore_it(self):
        sid=self.make();self.store.append(self.fx.user,sid,0,self.batch(),0)
        other=self.fx.app.test_client();self.fx.login(other,'other_user');self.assertEqual(other.get('/api/v1/records/'+sid+'/skeleton').status_code,404)
        version=self.store.summary(self.fx.user,sid)['state_version'];key=s.new_id()
        a=self.store.remove(self.fx.user,self.fx.auth,sid,version,key);b=self.store.remove(self.fx.user,self.fx.auth,sid,version,key);self.assertEqual(a,b)
        self.assertEqual(self.store.append(self.fx.user,sid,1,self.batch(20),0),'deleted');self.assertEqual(self.store.read(self.fx.user,sid)['frames'],[])
        self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['status'],'ready')

    def test_expiry_removes_data_without_deleting_record_or_resuming_capture(self):
        sid=self.make();self.store.append(self.fx.user,sid,0,self.batch(),0);self.fx.now[0]+=RETENTION_MS
        value=self.store.read(self.fx.user,sid);self.assertEqual(value['status'],'expired');self.assertEqual(value['frames'],[])
        self.assertEqual(self.store.append(self.fx.user,sid,1,self.batch(20),0),'expired');self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['session_id'],sid)
        with self.fx.db.transaction(False) as c:self.assertEqual(c.scalar(select(func.count()).select_from(s.skeleton_chunks)),0)

    def test_record_deletion_cascades_and_corrupt_chunk_is_not_exposed(self):
        sid=self.make();self.store.append(self.fx.user,sid,0,self.batch(),0)
        with self.fx.db.transaction() as c:c.execute(s.skeleton_chunks.update().where(s.skeleton_chunks.c.track_id==sid).values(payload=b'corrupt'))
        response=self.fx.client.get('/api/v1/records/'+sid+'/skeleton');self.assertEqual(response.status_code,503)
        self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());self.fx.finish(sid);v=self.fx.store.snapshot(self.fx.user,sid)['state_version']
        self.fx.store.delete_records(self.fx.user,self.fx.auth,[sid],{sid:v},s.new_id())
        with self.fx.db.transaction(False) as c:
            self.assertEqual(c.scalar(select(func.count()).select_from(s.skeleton_tracks)),0);self.assertEqual(c.scalar(select(func.count()).select_from(s.skeleton_chunks)),0)

    def test_schema8_upgrade_is_atomic_and_keeps_original_record(self):
        sid=self.make(False);self.fx.db.dispose()
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            for table in reversed(s.metadata.sorted_tables):
                if s.TABLE_VERSIONS.get(table.name,1)>8:c.execute('DROP TABLE "'+table.name+'"')
            c.execute('UPDATE app_meta SET schema_version=8');c.commit()
        with patch.object(s.skeleton_chunks,'create',side_effect=RuntimeError('synthetic migration fault')):
            with self.assertRaises(StorageError):self.fx.db.initialize(allow_round5_migration=True)
        with closing(sqlite3.connect(self.fx.db.path)) as c:
            self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],8);self.assertEqual(c.execute("SELECT count(*) FROM sqlite_master WHERE name='skeleton_tracks'").fetchone()[0],0)
        self.fx.db.initialize(allow_round5_migration=True);self.assertEqual(self.fx.db.inspect_identity()['schema_version'],s.SCHEMA_VERSION);self.assertEqual(self.fx.store.snapshot(self.fx.user,sid)['session_id'],sid)

    def test_schema9_requires_unique_chunk_order_index(self):
        with self.fx.db.transaction() as c:c.exec_driver_sql('DROP INDEX ix_skeleton_chunk_order')
        with self.assertRaises(StorageError):self.fx.db.inspect_identity()

    def test_unconfirmed_close_is_not_left_as_actively_saving_forever(self):
        sid=self.make();self.store.append(self.fx.user,sid,0,self.batch(),0)
        self.fx.store.set_state(self.fx.user,sid,'running',s.new_id());self.fx.finish(sid);self.fx.now[0]+=6000
        value=self.store.summary(self.fx.user,sid);self.assertEqual(value['status'],'interrupted');self.assertEqual(value['reason'],'closing_unconfirmed');self.assertEqual(value['frame_count'],20)


if __name__=='__main__':unittest.main()
