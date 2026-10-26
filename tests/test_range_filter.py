import sys,unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from uwb3d_core import AdaptiveRangeFilter,Matrix,Config,Range

class RangeFilterTests(unittest.TestCase):
    def test_redundant_triangle_guard(self):
        m=Matrix(Config(range_filtering=True));points=np.array([[0,0],[1,0],[0,1],[1,1],[.5,.3]])
        for i in range(5):
            for j in range(i+1,5):m.update(Range(i+1,j+1,float(np.linalg.norm(points[i]-points[j])),0,0,1))
        self.assertEqual(len(m.arrays(list(range(1,6)),0)[2]),10)
        # A sustained long bias cannot be fixed by temporal smoothing alone.
        m.links[(1,2)].distance=3.
        d,age,edges=m.arrays(list(range(1,6)),0)
        self.assertTrue(np.isnan(d[0,1]));self.assertEqual(len(edges),9)
        self.assertEqual(m.links[(1,2)].distance,3.) # raw link is retained
    def test_stationary_noise_and_motion(self):
        f=AdaptiveRangeFilter();rng=np.random.default_rng(82)
        raw=1+rng.normal(0,.03,200);out=[f.update(x,i*.2) for i,x in enumerate(raw)]
        self.assertLess(np.std(out[30:]),np.std(raw[30:])*.7)
        # Sustained 0.5 m change must pass, not remain stuck as an outlier.
        moved=[f.update(1.5,40+i*.2) for i in range(8)]
        self.assertGreater(moved[-1],1.47)
        # Ramp response at 0.3 m/s; bound lag error after startup.
        f=AdaptiveRangeFilter();errors=[]
        for i in range(100):
            x=1+.3*i*.2;y=f.update(x,i*.2)
            if y is not None:errors.append(abs(x-y))
        self.assertLess(max(errors[10:]),.08)
    def test_spike_staleness_duplicates_and_gap(self):
        m=Matrix(Config(range_filtering=True,max_age=2))
        for i in range(8):self.assertTrue(m.update(Range(1,2,1.,i*.2,i*.2,i)))
        self.assertFalse(m.update(Range(2,1,2.,1.6,1.6,8)))
        self.assertAlmostEqual(m.links[(1,2)].effective,1.4)
        self.assertFalse(m.update(Range(1,2,1.,1.6,1.6,8)))
        self.assertTrue(m.update(Range(1,2,1.01,1.8,1.8,9)))
        self.assertEqual(m.links[(1,2)].raw_distance,1.01)
        self.assertTrue(m.update(Range(1,2,3.,5.,5.,10)))
        self.assertEqual(m.links[(1,2)].distance,3.)
