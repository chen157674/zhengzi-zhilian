"""真实上传、图片标准化、本人隔离与文件／资料提交边界。"""
from contextlib import closing,contextmanager
from io import BytesIO
from pathlib import Path
import sqlite3
import struct
import uuid
import unittest
from unittest.mock import patch
import zlib
from PIL import Image
from sqlalchemy import select
from fitness_app import db_schema as s
from fitness_app.avatar_upload import MAX_AVATAR_BYTES
from fitness_app.database import Database,StorageError
import test_formal_records as shared


def picture(format='PNG',size=(160,120)):
    stream=BytesIO();image=Image.new('RGB',size,(40,110,80))
    if format=='JPEG':
        exif=Image.Exif();exif[315]='private author';image.save(stream,format=format,exif=exif)
    else:image.save(stream,format=format)
    return stream.getvalue()


class AvatarUploadTests(unittest.TestCase):
    def setUp(self):
        self.fx=shared.RecordTests();self.fx.setUp();self.root=Path(self.fx.app.config['AVATAR_STORAGE_ROOT'])
        self.profile=self.fx.client.get('/api/v1/account/profile').json['data']
    def tearDown(self):self.fx.tearDown()
    def upload(self,payload,version=None,client=None):
        client=client or self.fx.client;csrf=client.get('/api/v1/auth/csrf').json['data']['csrf_token']
        boundary='avatar-test-'+uuid.uuid4().hex
        version=str(self.profile['profile_version'] if version is None else version)
        raw=(f'--{boundary}\r\nContent-Disposition: form-data; name="expected_version"\r\n\r\n{version}\r\n'
             f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="../../do-not-use-this-name.png"\r\n'
             'Content-Type: image/png\r\n\r\n').encode()+payload+f'\r\n--{boundary}--\r\n'.encode()
        return client.post('/api/v1/account/avatar',data=raw,content_type=f'multipart/form-data; boundary={boundary}',headers={'X-CSRFToken':csrf})

    def test_upload_replaces_own_avatar_and_strips_metadata_for_all_formats(self):
        version=self.profile['profile_version']
        for format in ('PNG','JPEG','WEBP'):
            response=self.upload(picture(format),version);self.assertEqual(response.status_code,200,response.json)
            data=response.json['data'];version+=1;self.assertEqual(data['profile_version'],version)
            self.assertEqual(data['avatar_source'],'uploaded');self.assertNotIn('avatar_key',data)
            self.assertEqual(data['avatar_id'],self.profile['avatar_id'])
            with self.fx.client.get(data['avatar_url']) as result:
                self.assertEqual(result.status_code,200);self.assertEqual(result.mimetype,'image/jpeg')
                self.assertIn('no-store',result.headers['Cache-Control'])
                with Image.open(BytesIO(result.data)) as decoded:
                    self.assertEqual(decoded.size,(512,512));self.assertFalse(decoded.getexif())
            files=list(self.root.iterdir());self.assertEqual(len(files),1);self.assertEqual(files[0].suffix,'.jpg')
        self.assertEqual(self.fx.client.get('/api/v1/auth/me').json['data']['avatar_source'],'uploaded')

    def test_invalid_oversize_and_dimension_bomb_preserve_old_profile(self):
        huge=bytearray(picture());huge[16:24]=struct.pack('>II',4097,4097);huge[29:33]=struct.pack('>I',zlib.crc32(huge[12:29])&0xffffffff)
        for raw,status in [(b'<svg xmlns="http://www.w3.org/2000/svg"/>',422),(b'invalid-image',422),(picture(size=(32,32)),422),
                           (bytes(huge),422),(b'x'*(MAX_AVATAR_BYTES+1),413)]:
            response=self.upload(raw);self.assertEqual(response.status_code,status,response.json)
        self.assertEqual(self.profile,self.fx.client.get('/api/v1/account/profile').json['data'])
        self.assertFalse(self.root.exists() and any(self.root.iterdir()))

    def test_anonymous_and_other_users_cannot_get_the_uploaded_picture(self):
        self.assertEqual(self.upload(picture()).status_code,200)
        anonymous=self.fx.app.test_client();self.assertEqual(anonymous.get('/api/v1/account/avatar').status_code,401)
        self.assertEqual(self.upload(picture(),client=anonymous).status_code,401)
        other=self.fx.app.test_client();self.fx.login(other,'Other_User')
        self.assertEqual(other.get('/api/v1/account/avatar?user_id='+self.fx.user).status_code,404)
        self.assertEqual(other.get('/api/v1/account/avatar/'+self.fx.user).status_code,404)

    def test_stale_profile_version_does_not_replace_existing_file(self):
        response=self.upload(picture());version=response.json['data']['profile_version'];old=list(self.root.iterdir())[0].read_bytes()
        self.fx.post(self.fx.client,'/api/v1/account/profile',{'nickname':'刚改过昵称','expected_version':version})
        self.assertEqual(self.upload(picture('JPEG'),version).status_code,409)
        self.assertEqual(len(list(self.root.iterdir())),1);self.assertEqual(list(self.root.iterdir())[0].read_bytes(),old)

    def test_failed_file_write_preserves_previous_avatar(self):
        first=self.upload(picture());version=first.json['data']['profile_version'];old=list(self.root.iterdir())[0]
        original=Path.open
        def fail(path,*args,**kwargs):
            if args and args[0]=='xb' and path.parent==self.root:raise OSError('D-FAULT avatar write')
            return original(path,*args,**kwargs)
        with patch.object(Path,'open',fail):response=self.upload(picture('JPEG'),version)
        self.assertEqual(response.status_code,503,response.json);self.assertTrue(old.exists());self.assertEqual(len(list(self.root.iterdir())),1)
        self.assertEqual(self.fx.client.get('/api/v1/account/profile').json['data']['profile_version'],version)

    def test_failed_database_commit_removes_only_new_unreferenced_copy(self):
        first=self.upload(picture());version=first.json['data']['profile_version'];old=list(self.root.iterdir())[0]
        transaction=self.fx.db.transaction
        @contextmanager
        def fail(write=True):
            with transaction(write) as c:
                yield c
                if write:raise StorageError('D-FAULT avatar commit')
        with patch.object(self.fx.db,'transaction',fail):response=self.upload(picture('JPEG'),version)
        self.assertEqual(response.status_code,503,response.json)
        self.assertTrue(old.exists());self.assertEqual(list(self.root.iterdir()),[old])
        self.assertEqual(self.fx.client.get('/api/v1/account/profile').json['data']['profile_version'],version)

    def test_existing_identifier_collision_never_deletes_another_users_avatar(self):
        key='a'*32+'.jpg';self.root.mkdir(parents=True,exist_ok=True)
        existing=self.root/key;raw=picture('JPEG');existing.write_bytes(raw)
        with self.fx.db.transaction() as c:
            c.execute(s.user_profiles.insert().values(user_id=self.fx.other,nickname='其他用户',avatar_id='avatar-01',avatar_key=key))
        with patch('fitness_app.account.uuid.uuid4',return_value=uuid.UUID('a'*32)):
            response=self.upload(picture())
        self.assertEqual(response.status_code,503,response.json)
        self.assertTrue(existing.exists());self.assertEqual(existing.read_bytes(),raw)
        self.assertEqual(self.fx.client.get('/api/v1/account/profile').json['data']['avatar_source'],'builtin')

    def legacy_three(self):
        self.fx.db.dispose()
        with closing(sqlite3.connect(str(self.fx.db.path))) as c,c:
            c.execute('BEGIN');c.execute('DROP INDEX IF EXISTS ix_receipts_resource_saved')
            for table in reversed(s.metadata.sorted_tables):
                if s.TABLE_VERSIONS.get(table.name,1)>3:c.execute('DROP TABLE "'+table.name+'"')
            c.execute('ALTER TABLE user_profiles DROP COLUMN avatar_key');c.execute('UPDATE app_meta SET schema_version=3')

    def test_schema_three_upgrades_without_changing_nickname_or_login(self):
        self.legacy_three();database=Database(self.fx.db.path)
        try:
            self.assertEqual(database.initialize(allow_round5_migration=True)['schema_version'],s.SCHEMA_VERSION)
            result=self.fx.client.get('/api/v1/account/profile').json['data']
            self.assertEqual(result['nickname'],self.profile['nickname']);self.assertEqual(result['username'],self.profile['username'])
            self.assertEqual(result['avatar_source'],'builtin')
        finally:database.dispose()

    def test_schema_three_failed_upgrade_rolls_back_added_column(self):
        self.legacy_three();database=Database(self.fx.db.path)
        try:
            with patch('fitness_app.database.update',side_effect=RuntimeError('D-FAULT after avatar column')):
                with self.assertRaises(StorageError):database.initialize(allow_round5_migration=True)
            with closing(sqlite3.connect(str(self.fx.db.path))) as c:
                self.assertEqual(c.execute('SELECT schema_version FROM app_meta').fetchone()[0],3)
                self.assertNotIn('avatar_key',[r[1] for r in c.execute('PRAGMA table_info(user_profiles)')])
                self.assertEqual(c.execute('SELECT nickname FROM user_profiles WHERE user_id=?',(self.fx.user,)).fetchone()[0],self.profile['nickname'])
        finally:database.dispose()


if __name__=='__main__':unittest.main()
