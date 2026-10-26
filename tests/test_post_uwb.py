import sys,unittest,math,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cockroach_mapping import BehaviorMap
from position_gate import PositionGate
from test_virtual_geofencing import state

class PostUWBTests(unittest.TestCase):
 def test_spike_rejected_source_unchanged(self):
  gate=PositionGate();s=state(10,x=0);before=copy.deepcopy(s);gate.update(s,1,10);self.assertEqual(s,before)
  s=state(10.1,x=2);filtered=gate.update(s,1,10.1)
  self.assertEqual(filtered['positions'][1],[0,0,0]);self.assertEqual(s['positions'][1][0],2)
  self.assertIn('1',filtered['position_rejections'])
  self.assertIn(1,gate.update(state(10.2,x=.01),1,10.2)['positions'])
 def test_slow_motion_kept(self):
  gate=PositionGate()
  for i in range(30):self.assertIn(1,gate.update(state(10+i*.1,x=i*.02,moving=True),1,10+i*.1)['positions'])
 def test_no_timeout_uncertainty_or_term_bypass(self):
  gate=PositionGate();gate.update(state(10,x=0),1,10)
  for i in range(1,100):
   t=10+i*.1;s=state(t,x=2);s['network']['term']=i;s['uncertainty']=.14
   out=gate.update(s,1,t)
   self.assertEqual(out['positions'][1],[0,0,0])
   self.assertEqual(out['node_states'][1],'outlier-rejected')
  out=gate.update(state(100,x=2),1,100)
  self.assertEqual(out['positions'][1],[0,0,0])
 def test_no_sigma_allowance_at_high_rate(self):
  gate=PositionGate();gate.update(state(10,x=0),1,10)
  out=gate.update(state(10.04,x=.1),1,10.04)
  self.assertEqual(out['positions'][1][0],0)
 def test_rejected_samples_never_recorded(self):
  from live_trails import LiveTrails
  from virtual_geofencing import Mission
  gate=PositionGate();trails=LiveTrails();mission=Mission()
  mission.start(state(10,x=0),1,10)
  for i in range(30):
   t=10+i*.1;s=gate.update(state(t,x=0 if i==0 else (2 if i%2 else 4)),1,t)
   trails.update(s,1,t);mission.update(s,1,t)
  self.assertEqual(len(trails.rows[1]),1)
  self.assertEqual(len(mission.tracks[1]),1)
 def test_shared_view_waits_for_full_fleet(self):
  from uwb3d_view import UWB3DView
  from types import SimpleNamespace
  from unittest.mock import patch
  gate=PositionGate();current=state(10,x=0)
  runtime=SimpleNamespace(source='swarm',generation=1,snapshot=lambda:current)
  view=SimpleNamespace(runtime=runtime,position_gate=gate,gate_start=None,gate_generation=None,count=SimpleNamespace(get=lambda:3))
  initial=current;current={}
  self.assertEqual(UWB3DView.filtered_snapshot(view),{})
  current=initial
  with patch('uwb3d_view.time.monotonic',return_value=10):
   self.assertFalse(UWB3DView.filtered_snapshot(view)['positions']);self.assertFalse(gate.last)
  current['positions'][2]=[.3,0,0];current['node_states'][2]='measured-supported'
  with patch('uwb3d_view.time.monotonic',return_value=10):
   self.assertFalse(UWB3DView.filtered_snapshot(view)['positions'])
  current['time']=11.1;current['published_at']=11.1
  with patch('uwb3d_view.time.monotonic',return_value=11.1):
   self.assertEqual(len(UWB3DView.filtered_snapshot(view)['positions']),3)
 def test_reacquire_stable_remote_cluster_without_recording_jump(self):
  gate=PositionGate();gate.update(state(10,x=0),1,10)
  reacquired=[]
  for i in range(1,21):
   t=10+i*.1;out=gate.update(state(t,x=2),1,t)
   if out['position_reacquired']:
    reacquired.append(out);self.assertEqual(out['node_states'][1],'reacquired')
  self.assertEqual(len(reacquired),1);self.assertEqual(out['positions'][1][0],2)
 def test_missing_node_keeps_last_marker(self):
  gate=PositionGate();gate.update(state(10,x=.2),1,10)
  s=state(11);s['positions'].pop(1)
  out=gate.update(s,1,11);self.assertEqual(out['positions'][1][0],.2)
  self.assertIn('SIGNAL LOST',out['position_rejections']['1'])
 def test_slow_loop_and_nearby_target_fusion(self):
  m=BehaviorMap()
  for i in range(151):
   a=i*2*math.pi/150
   m.observe(1,dict(t=i*.1,xy=[.125+.35*math.cos(a),.125+.35*math.sin(a)],sigma=.02,segment=1),True)
  self.assertTrue(any(e['kind']=='circling' for e in m.events))
  m=BehaviorMap()
  for n,t,x in [(1,0,.24),(2,12,.26),(1,24,.23)]:
   m.add_evidence(n,t,[x,.125],[0,0],.02,'blocked_approach',1.)
  self.assertEqual(len(m.hypotheses),1)
  self.assertTrue(any(r['state']=='SUSPECTED OBSTRUCTION' for r in m.cells()))
 def test_clear_trajectories_retains_map_and_live_mission(self):
  from virtual_geofencing import Mission
  m=Mission();m.start(state(10),1,10);m.update(state(10),1,10)
  m.behavior_map.passage[(0,0)]=dict(visits=1,nodes={1},last=10)
  m.clear_trajectories(11)
  self.assertFalse(m.tracks);self.assertFalse(m.last_good);self.assertTrue(m.running)
  self.assertTrue(m.behavior_map.passage)
  m.update(state(11.1),1,11.1);self.assertEqual(len(m.tracks[1]),1)
 def test_quiet_stops_slow_drift_and_motion_releases(self):
  gate=PositionGate();locked=None
  for i in range(50):
   t=10+i*.1;out=gate.update(state(t,x=i*.001),1,t)
   if i==10:locked=out['positions'][1][0]
   if i>10:self.assertEqual(out['positions'][1][0],locked)
  self.assertIn(1,out['stationary_held'])
  out=gate.update(state(15,x=locked+.02,moving=True),1,15)
  self.assertEqual(out['positions'][1][0],locked+.02)
  self.assertNotIn(1,out['stationary_held'])
 def test_stale_imu_does_not_freeze(self):
  gate=PositionGate()
  for i in range(10):
   t=10+i*.1;s=state(t,x=i*.01);s['nodes'][0]['imu']['received']=1
   out=gate.update(s,1,t)
   self.assertNotIn(1,out['stationary_held'])
   self.assertAlmostEqual(out['positions'][1][0],i*.01)
 def test_quiet_lock_does_not_survive_missing_node(self):
  gate=PositionGate()
  for i in range(10):gate.update(state(10+i*.1,x=0),1,10+i*.1)
  self.assertIn(1,gate.stationary)
  missing=state(11);missing['positions'].pop(1);gate.update(missing,1,11)
  self.assertNotIn(1,gate.stationary)
  for i in range(21):
   t=11.1+i*.1;out=gate.update(state(t,x=1),1,t)
  self.assertEqual(out['positions'][1][0],1)
 def circle(self,m,node,start):
  for i in range(61):
   angle=i*2*math.pi/60
   m.observe(node,dict(t=start+i*.09,xy=[.125+.35*math.cos(angle),.125+.35*math.sin(angle)],sigma=.02,segment=1),True)
 def test_circling_evidence_and_repeat_promotion(self):
  m=BehaviorMap();self.circle(m,1,0)
  self.assertTrue(any(e['kind']=='circling' for e in m.events))
  self.assertFalse(any(r['state']=='SUSPECTED OBSTRUCTION' for r in m.cells()))
  self.circle(m,2,12);self.circle(m,1,24)
  self.assertTrue(any(r['state']=='SUSPECTED OBSTRUCTION' for r in m.cells()))
 def test_blocked_approach_and_quiet_control(self):
  for moving in (False,True):
   m=BehaviorMap()
   for i in range(51):
    m.observe(1,dict(t=i*.1,xy=[min(i*.02,.4),0],sigma=.02,segment=1),moving)
   self.assertEqual(any(e['kind']=='blocked_approach' for e in m.events),moving)

if __name__=='__main__':unittest.main()
