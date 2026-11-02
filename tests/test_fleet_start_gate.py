import sys,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from uwb3d_view import UWB3DView
class FleetGateTests(unittest.TestCase):
 def test_small_fleet_not_blocked_by_default_simulation_count(self):
  raw={'positions':{1:[0,0,0],2:[1,0,0],3:[0,1,0]},'error':''}
  view=SimpleNamespace(runtime=SimpleNamespace(snapshot=lambda:raw,generation=1,source='swarm',gateway=SimpleNamespace(count=3)),gate_generation=1,gate_start=None,count=SimpleNamespace(get=lambda:30),position_gate=SimpleNamespace(update=lambda source,*args:source))
  with patch('uwb3d_view.time.monotonic',side_effect=[100.,101.1]):
   self.assertEqual(UWB3DView.filtered_snapshot(view)['positions'],{})
   self.assertEqual(len(UWB3DView.filtered_snapshot(view)['positions']),3)
  self.assertEqual(view.gate_start,'ready')
if __name__=='__main__':unittest.main()
