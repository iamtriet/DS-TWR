import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from uwb3d_core import six_neighbor_mask,Range

class SparseNeighborTests(unittest.TestCase):
    def test_noisy_peer_deprioritized_but_probed(self):
        history={(p,6):[(i*.1,1+(.15*(-1)**i if p==3 else .001*(-1)**i)) for i in range(15)] for p in range(1,6)}
        links={(p,6):Range(p,6,1,1.4,1.4,1) for p in range(1,6)}
        masks=[six_neighbor_mask(history,links,1.5,i,2)[0] for i in range(12)]
        self.assertFalse(masks[0] & 4);self.assertTrue(masks[2] & 4)
        self.assertTrue(all(m.bit_count()==4 for m in masks))
        for start in range(10):
            self.assertEqual(masks[start]|masks[start+1]|masks[start+2],31)
    def test_linear_motion_and_missing_links(self):
        history={(p,6):[(t,1+t*.4) for t in (0,.1,.3,.4,.8)] for p in range(1,6)}
        links={(p,6):Range(p,6,1,.8,.8,1) for p in range(1,6)}
        _,scores=six_neighbor_mask(history,links,.9,0,2)
        self.assertLess(max(scores.values())-min(scores.values()),1e-10)
        masks=[six_neighbor_mask({}, {},0,i,2)[0] for i in range(3)]
        self.assertEqual(masks[0]|masks[1]|masks[2],31)
