import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from trajectory_guard import TrajectoryGuard
from cockroach_mapping import BehaviorMap
from automatic_fences import AutomaticFences

class MotionZoneTests(unittest.TestCase):
    def test_jump_does_not_become_trusted_on_next_sample(self):
        g=TrajectoryGuard();g.check(1,0,[0,0],.02,True,.3)
        for i in range(1,8):
            result,_=g.check(1,i*.1,[2,0],.02,True,.3)
            self.assertEqual(result,'SPEED EXCEEDED')
    def test_window_detects_fast_small_steps(self):
        g=TrajectoryGuard();results=[]
        for i in range(15):results.append(g.check(1,i*.1,[i*.06,0],.02,True,.3)[0])
        self.assertIn('SPEED EXCEEDED',results)
    def test_reacquisition_cannot_accept_continued_overspeed(self):
        g=TrajectoryGuard();g.check(1,0,[0,0],.02,True,.3)
        for i in range(1,50):
            result,_=g.check(1,i*.1,[2+i*.06,0],.02,True,.3)
            self.assertEqual(result,'SPEED EXCEEDED')
    def test_coherent_reacquisition_opens_gap(self):
        g=TrajectoryGuard();g.check(1,0,[0,0],.02,True,.3)
        results=[g.check(1,i*.1,[2,0],.02,True,.3)[0] for i in range(1,15)]
        self.assertIn('REACQUIRING / PATH GAP',results)
        self.assertEqual(results[-1],'ACCEPTED')
    def test_valid_motion_and_jitter_accepted(self):
        g=TrajectoryGuard()
        for i in range(30):self.assertEqual(g.check(1,i*.1,[i*.02,0],.02,True,.3)[0],'ACCEPTED')
        g=TrajectoryGuard()
        for i in range(30):self.assertEqual(g.check(1,i*.1,[.01*(i%2),0],.02,False,.3)[0],'ACCEPTED')
    def test_quiet_drift_remains_rejected_until_return(self):
        g=TrajectoryGuard();results=[]
        for i in range(40):results.append(g.check(1,i*.1,[i*.01,0],.02,False,1)[0])
        self.assertIn('DRIFT SUSPECTED / IMU QUIET',results)
        self.assertEqual(g.check(1,4,[0,0],.02,False,1)[0],'ACCEPTED')
    def test_zones_expire_and_never_create_obstacles(self):
        m=BehaviorMap()
        for i in range(42):m.observe(1,dict(t=i*.1,xy=[0,0],sigma=.02,segment=1),False)
        self.assertIn('STATIONARY ZONE',[r['state'] for r in m.cells()])
        for i in range(42):m.observe(2,dict(t=5+i*.1,xy=[1,0],sigma=.02,segment=1),True)
        self.assertIn('LOW PROGRESS ZONE',[r['state'] for r in m.cells()])
        self.assertFalse(m.hypotheses)
        restored=BehaviorMap.load(m.document());self.assertEqual(restored.cells(),m.cells())
        m.advance(50);self.assertFalse(m.cells())
    def test_mission_drift_does_not_extend_trace_or_allow_marker(self):
        from virtual_geofencing import Mission
        from test_virtual_geofencing import state
        m=Mission();m.biological_enabled=True;m.start(state(10),1,10)
        for i in range(40):m.update(state(10+i*.1,x=i*.01,moving=False),1,10+i*.1)
        count=len(m.tracks[1]);m.update(state(14.1,x=.42,moving=False),1,14.1)
        self.assertEqual(len(m.tracks[1]),count)
        self.assertIn('DRIFT SUSPECTED',m.status[1]['behavior'])
        self.assertFalse(m.behavior_map.passage)
        with self.assertRaises(ValueError):m.mark(1,'suspect')
    def test_frontier_not_blocked_and_no_fill_across_gap(self):
        m=BehaviorMap();m.passage={(0,0):dict(visits=1,nodes={1},last=0),(5,0):dict(visits=1,nodes={1},last=0)}
        f=AutomaticFences();f.update(m)
        self.assertFalse(f.exclusions);self.assertNotIn((2,0),f.explored);self.assertIn((1,0),f.frontier)

if __name__=='__main__':unittest.main()
