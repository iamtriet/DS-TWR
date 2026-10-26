import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from swarm_range_tracker import RangeDomainTracker
from uwb3d_core import Range


class RangeTrackerTests(unittest.TestCase):
    def test_two_tags_keep_independent_states_and_measurement_history(self):
        tracker=RangeDomainTracker();anchors=np.array([[-1.,0.],[1.,0.],[0.,1.5]])
        for i in range(60):
            now=i*.04
            for node,truth in ((2,np.array([i*.004,.4])),(5,np.array([-.4,.7]))):
                records=[Range(node,j+10,float(np.linalg.norm(truth-a)),now,now,i,quality=1.) for j,a in enumerate(anchors)]
                before=tracker.states.get(2)
                if before is not None:before=(before[0].copy(),before[1].copy(),before[2])
                tracker.update(node,anchors,records,truth,stationary=node==5)
                if node==5 and before is not None:
                    np.testing.assert_array_equal(before[0],tracker.states[2][0])
                    np.testing.assert_array_equal(before[1],tracker.states[2][1])
        self.assertLess(np.linalg.norm(tracker.states[2][0][:2]-[.236,.4]),.03)
        np.testing.assert_allclose(tracker.states[5][0][:2],[-.4,.7],atol=1e-8)

    def test_motion_stop_and_single_bad_range(self):
        rng=np.random.default_rng(7);tracker=RangeDomainTracker()
        anchors=np.array([[-1.,0.],[1.,0.],[0.,1.5]])
        errors=[]
        for i in range(250):
            now=i*.04;truth=np.array([min(max(now-1.,0)*.2,.8),.4]);records=[]
            for j,a in enumerate(anchors):
                stamp=now-j*.005;past=np.array([min(max(stamp-1.,0)*.2,.8),.4])
                distance=np.linalg.norm(past-a)+rng.normal(0,.02)+(1.5 if i==70 and j==0 else 0.)
                records.append(Range(2,j+10,distance,stamp,now,i,quality=1.))
            p,_,_=tracker.update(2,anchors,records,np.array([0.,.4]),now<1 or now>5.8)
            errors.append(np.linalg.norm(p-truth))
            covariance=tracker.states[2][1].copy()
            again,_,meta=tracker.update(2,anchors,records,np.zeros(2))
            np.testing.assert_array_equal(p,again)
            np.testing.assert_array_equal(covariance,tracker.states[2][1])
            self.assertEqual(meta['range_updates'],0)
            self.assertGreaterEqual(np.linalg.eigvalsh(covariance).min(),-1e-10)
        self.assertLess(np.sqrt(np.mean(np.square(errors))),.04)
        self.assertLess(errors[-1],.03)


if __name__=='__main__':unittest.main()
