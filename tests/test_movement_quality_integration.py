"""16新增有向语义在真实非正方形姿态缓存上的镜像／旋转检查。"""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from prepare_movement_input import cached_frames
from fitness_app.movement_quality import hip_offset


class MovementQualityInputTests(unittest.TestCase):
    def test_real_plank_cached_geometry_keeps_sign_after_mirror_and_rotation(self):
        frames=cached_frames('dvids-640237-side');checked=0
        for f in frames:
            value=hip_offset(f,'left',(0,-1))
            if value is None:continue
            for rotate in (False,True):
                transformed=copy.deepcopy(f)
                if rotate:transformed['width'],transformed['height']=f['height'],f['width']
                for point in transformed['framing']['landmarks']:
                    if 'x' not in point:continue
                    point['x'],point['y']=(1-point['y'],point['x']) if rotate else (1-point['x'],point['y'])
                self.assertAlmostEqual(hip_offset(transformed,'left',(1,0) if rotate else (0,-1)),value,places=10)
            checked+=1
        self.assertGreater(checked,200)


if __name__=='__main__':unittest.main()
