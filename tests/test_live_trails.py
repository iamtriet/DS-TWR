import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from live_trails import LiveTrails

def snap(t,x=0,term=1):
 return dict(time=t,published_at=t,positions={'1':[x,0,0],'6':[0,0,0]},nodes=[dict(id=1,last=t)],metrics=dict(origin_node=6),network=dict(term=term))

class LiveTrailTests(unittest.TestCase):
 def test_live_estimates_without_mission(self):
  h=LiveTrails();h.update(snap(1),1,1);h.update(snap(1.2,.1),1,1.2)
  self.assertEqual(len(h.rows[1]),2);self.assertNotIn(6,h.rows)
 def test_no_bridge_gap_jump_or_frame(self):
  h=LiveTrails()
  for t,x in [(1,0),(1.2,.1),(2,.2),(2.2,2)]:h.update(snap(t,x),1,t)
  self.assertEqual([r['segment'] for r in h.rows[1]],[1,1,2,3])
  h.update(snap(3,0,term=2),1,3);self.assertEqual(len(h.rows[1]),1)
 def test_stale_duplicate_and_bounded_history(self):
  h=LiveTrails(3);h.update(snap(1),1,2);self.assertFalse(h.rows)
  for i in range(8):h.update(snap(3+i*.2),1,3+i*.2)
  h.update(snap(4.4),1,4.4);self.assertEqual(len(h.rows[1]),3)

if __name__=='__main__':unittest.main()
