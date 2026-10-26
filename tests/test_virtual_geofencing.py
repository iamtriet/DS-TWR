import sys,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from virtual_geofencing import Mission,BioProfile,fence_state

def state(t,x=.1,good=True,moving=False):
    return dict(time=t,published_at=t,age=.08,positions={1:[x,0,0],6:[0,0,0]},node_states={1:'measured-supported' if good else 'stale',6:'measured-supported'},uncertainty=.03,
        nodes=[dict(id=n,uid=str(n),last=t,imu=dict(moving=moving if n==1 else False,received=t)) for n in (1,6)],
        network=dict(term=7),metrics=dict(origin_node=6,geometry_inconsistent=False,node_quality={}))

class GeofencingTests(unittest.TestCase):
    def mission(self):
        m=Mission();m.set_rectangle(-1,-1,1,1);m.start(state(10),1,10);return m
    def test_boundary_uncertainty(self):
        m=self.mission()
        self.assertEqual(fence_state([0,0],m.polygon,.2),'INSIDE')
        self.assertEqual(fence_state([1.1,0],m.polygon,.2),'BOUNDARY / UNCERTAIN')
        self.assertEqual(fence_state([1.5,0],m.polygon,.2),'OUTSIDE')
    def test_no_bridging_stale_gap_and_partial_route(self):
        m=self.mission();m.update(state(10),1,10);m.update(state(10.1,good=False),1,10.1);m.update(state(10.2,.12),1,10.2)
        self.assertEqual([r['segment'] for r in m.tracks[1]],[1,2])
        self.assertIn('partial',m.mark(1,'candidate')['route_status'])
    def test_complete_trace_requires_start_near_origin(self):
        m=self.mission()
        for i in range(5):m.update(state(10+i*.2,.1+i*.04,moving=True),1,10+i*.2)
        mark=m.mark(1,'operator saw victim',True)
        self.assertTrue(mark['confirmed']);self.assertIn('continuous',mark['route_status'])
        self.assertEqual(mark['human_passability'],'not assessed')
    def test_unknown_geometry_and_old_snapshot_not_recorded(self):
        m=self.mission();s=state(10);s['metrics']['geometry_inconsistent']=True;m.update(s,1,10)
        self.assertFalse(m.tracks)
        m.update(state(10),1,11);self.assertFalse(m.tracks)
        with self.assertRaises(ValueError):m.mark(1,'no reliable position')
    def test_frame_or_origin_motion_requires_new_mission(self):
        m=self.mission()
        for i in range(12):
            t=10.1+i*.1;s=state(t);s['nodes'][1]['imu']['moving']=True;m.update(s,1,t)
        self.assertTrue(m.running);self.assertTrue(m.origin_motion_hold)
        m.update(state(12),1,12)
        self.assertTrue(m.running);self.assertFalse(m.origin_motion_hold)
        self.assertFalse(m.archive_review)
        m=self.mission();s=state(10.1);s['network']['term']=8;m.update(s,1,10.1);self.assertFalse(m.running)
    def test_brief_origin_motion_recovers_without_bridging(self):
        m=self.mission();m.update(state(10),1,10)
        for i in range(7):
            t=10.1+i*.1;s=state(t);s['nodes'][1]['imu']['moving']=True;m.update(s,1,t)
        self.assertTrue(m.running);self.assertTrue(m.origin_motion_hold)
        m.update(state(10.8),1,10.8)
        self.assertTrue(m.running);self.assertFalse(m.origin_motion_hold)
        self.assertEqual([r['segment'] for r in m.tracks[1]],[1,2])
    def test_repeated_sample_and_stale_imu_cannot_confirm_motion(self):
        m=self.mission();s=state(10.1);s['nodes'][1]['imu']['moving']=True
        for i in range(20):m.update(s,1,10.1+i*.1)
        self.assertTrue(m.running);self.assertTrue(m.origin_motion_hold)
        self.assertFalse(m.tracks)
        m.update(state(12.2),1,12.2);self.assertFalse(m.origin_motion_hold)
    def test_biology_disabled_by_default_does_not_gate_tracking(self):
        m=self.mission();m.profiles[1]=BioProfile(.01,.01,.01)
        for i in range(8):m.update(state(10+i*.1,x=i*.03,moving=True),1,10+i*.1)
        self.assertFalse(m.biological_enabled)
        self.assertEqual(m.status[1]['bio'],'MOVING')
        self.assertIn(1,m.last_good)
        self.assertFalse(m.activity)
    def test_bio_budget_and_quiet_recovery(self):
        m=self.mission();m.biological_enabled=True;m.profiles[1]=BioProfile(1,.3,.3)
        for i in range(6):m.update(state(10+i*.1,moving=True),1,10+i*.1)
        self.assertEqual(m.status[1]['bio'],'REST ADVISED')
        for i in range(6,12):m.update(state(10+i*.1),1,10+i*.1)
        self.assertEqual(m.status[1]['bio'],'QUIET');self.assertEqual(m.activity[1],0.)
    def test_export_and_archive_review(self):
        m=self.mission();m.update(state(10),1,10);m.mark(1,'candidate')
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'mission.json';m.export(path);loaded=Mission.load(path)
            self.assertTrue(loaded.archive_review)
            self.assertEqual(loaded.markers,m.markers);self.assertTrue(path.with_suffix('.csv').exists())
            with self.assertRaises(ValueError):loaded.start(state(11),1,11)

if __name__=='__main__':unittest.main()
