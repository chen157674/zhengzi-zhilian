"""批量评分必须保持原串行模型、支持集和首胜顺序；不评健身准确率。"""
import unittest
import numpy as np
from fitness_app.body_lean import _score_axis_models


def serial_score(models,angles,weights,xs,ys,h,w):
    best=None
    for kind,local,vp in models:
        predicted=np.full(len(angles),local) if vp is None else np.degrees(np.arctan((vp[0]-xs)/(ys-vp[1])))
        residual=np.abs(predicted-angles);support=residual<=2.
        if support.sum()<4:continue
        fraction=float(weights[support].sum()/weights.sum())
        spread=float(np.ptp(xs[support]))
        if fraction<.65 or spread<.25*w or weights[support].sum()<.8*h:continue
        score=float(weights[support].sum())-(.025*h if vp is not None else 0.)
        if best is None or score>best[0]:best=(score,kind,local,vp,support,residual,fraction,spread)
    return best


class SceneScoringTests(unittest.TestCase):
    def test_same_support_groups_preserve_original_first_best_model_and_all_numeric_evidence(self):
        random=np.random.default_rng(20261103)
        for count in (8,17,64):
            for case in range(10):
                xs=random.uniform(0,640,count);ys=random.uniform(0,360,count)
                angles=random.normal(0,2,count);weights=random.uniform(40,160,count)
                models=[('parallel',float(value),None) for value in angles]
                models += [('perspective',float(random.normal()),(float(random.uniform(-400,1000)),float(random.uniform(-3000,-700)))) for _ in range(180)]
                expected=serial_score(models,angles,weights,xs,ys,360,640)
                actual=_score_axis_models(models,angles,weights,xs,ys,360,640)
                if expected is None:self.assertIsNone(actual);continue
                self.assertEqual(expected[:4],actual[:4])
                np.testing.assert_array_equal(expected[4],actual[4])
                np.testing.assert_array_equal(expected[5],actual[5])
                self.assertEqual(expected[6:],actual[6:])


if __name__=='__main__':unittest.main()
