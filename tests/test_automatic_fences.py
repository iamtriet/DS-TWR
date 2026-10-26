import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from automatic_fences import AutomaticFences
from virtual_geofencing import Mission
from cockroach_mapping import BehaviorMap
from test_virtual_geofencing import state

class AutomaticFenceTests(unittest.TestCase):
    def test_start_without_drawing_and_rest_does_not_create_fence(self):
        m=Mission();m.start(state(10),1,10)
        for i in range(30):m.update(state(10+i*.1),1,10+i*.1)
        self.assertFalse(m.automatic_fences.explored)
        self.assertEqual(m.status[1]['fence'],'LEARNING FENCE')
    def test_motion_builds_frontier_without_joining_unknown_space(self):
        m=Mission();m.start(state(10),1,10)
        for i in range(25):m.update(state(10+i*.1,x=.1+i*.03,moving=True),1,10+i*.1)
        self.assertTrue(m.automatic_fences.explored)
        self.assertFalse(m.automatic_fences.exclusions)
        self.assertEqual(m.automatic_fences.state((10,10),.2),'UNKNOWN FRONTIER')
    def test_union_preserves_hole_and_disconnected_cells(self):
        f=AutomaticFences(1)
        cells={(x,y) for x in range(3) for y in range(3)}-{(1,1)}
        self.assertEqual(len(f.edges(cells)),16)
        self.assertEqual(len(f.edges({(0,0),(4,4)})),8)
        self.assertEqual(len(f.edges({(0,0),(1,0)})),6)
    def test_warning_retracts_after_traversal(self):
        b=BehaviorMap();f=AutomaticFences()
        b.hypotheses[(2,2)]=dict(votes=[dict(node=1,t=0),dict(node=2,t=10),dict(node=1,t=22)],radius=.25,last=22,contradicted=False)
        f.update(b);self.assertEqual(f.state((.6,.6),.2),'CAUTION / SUSPECTED OBSTACLE')
        b.hypotheses[(2,2)]['contradicted']=True
        b.passage[(2,2)]=dict(visits=1,nodes={1},last=23)
        f.update(b);self.assertFalse(f.exclusions)
        self.assertEqual(f.state((.6,.6),.2),'OBSERVED AREA')
    def test_archive_rebuilds_and_manual_mode_remains(self):
        m=Mission();m.behavior_map.passage[(0,0)]=dict(visits=1,nodes={1},last=1)
        m.automatic_fences.update(m.behavior_map)
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'mission.json';m.save(p);loaded=Mission.load(p)
            self.assertTrue(loaded.auto_fence);self.assertEqual(loaded.automatic_fences.explored,{(0,0)})
            m.export(p)
            self.assertTrue(p.with_suffix('.evidence.csv').exists())
            self.assertIn('observed_frontier',p.with_suffix('.fences.csv').read_text())
        m.set_rectangle(-1,-1,1,1);self.assertFalse(m.auto_fence)

if __name__=='__main__':unittest.main()
