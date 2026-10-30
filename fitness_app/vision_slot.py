"""本机唯一视觉执行槽；预览、实时和离线任务共用，不覆盖他人占用。"""
import threading
from .auth import APIError


class VisionSlot:
    def __init__(self):self.lock=threading.RLock();self._owner=None

    def claim(self,user_id,resource_id,kind):
        with self.lock:
            owner=(user_id,resource_id,kind)
            if self._owner is not None and self._owner!=owner:raise APIError('VISION_BUSY','本机正在处理另一项视觉任务，请先结束原任务',409)
            self._owner=owner

    def release(self,user_id,resource_id,kind):
        with self.lock:
            if self._owner==(user_id,resource_id,kind):self._owner=None

    def transfer(self,user_id,old_id,old_kind,new_id,new_kind):
        with self.lock:
            if self._owner!=(user_id,old_id,old_kind):raise APIError('CONTROL_CONFLICT','原视觉占用已失效',409)
            self._owner=(user_id,new_id,new_kind)

    def owns(self,user_id,resource_id,kind):
        with self.lock:return self._owner==(user_id,resource_id,kind)
