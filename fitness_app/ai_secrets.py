"""项目内持久主密钥与按所有人绑定的API密钥密文；不回传明文。"""
import json,os
from cryptography.fernet import Fernet,InvalidToken
from .paths import project_path
from .database import StorageError,maintenance_lock


class SecretVault:
    def __init__(self,path):self.path=project_path(path)

    def cipher(self,create=False):
        if create:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with maintenance_lock(self.path):
                if not self.path.exists():
                    with self.path.open('xb') as stream:
                        stream.write(Fernet.generate_key());stream.flush();os.fsync(stream.fileno())
                    os.chmod(self.path,0o600)
        try:return Fernet(self.path.read_bytes())
        except (OSError,ValueError) as error:
            raise StorageError('AI主密钥不存在或无效；不会覆盖原密文，请恢复项目密钥或重新配置API密钥') from error

    def seal(self,owner,key,create=True):
        raw=json.dumps({'owner':owner,'key':key},separators=(',',':')).encode()
        return self.cipher(create=create).encrypt(raw).decode('ascii')

    def open(self,owner,token):
        try:
            value=json.loads(self.cipher().decrypt(token.encode('ascii')))
            if value.get('owner')!=owner or not isinstance(value.get('key'),str):raise ValueError('owner')
            return value['key']
        except (InvalidToken,ValueError,KeyError,UnicodeError,TypeError) as error:
            raise StorageError('API密钥密文无法核对，原配置保留，请重新设置') from error
