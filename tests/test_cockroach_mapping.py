import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cockroach_mapping import BehaviorMap

def sample(m,node,t,x,y=0,moving=True,sigma=.02,segment=1,rest=False):
    m.observe(node,dict(t=t,xy=[x,y],sigma=sigma,segment=segment),moving,rest)

def turnaround(m,node,t):
    # Observed approach to x=.8, then a retreat; hypothesis projects beyond tip.
    for i,x in enumerate([0,.1,.2,.3,.4,.5,.6,.7,.8,.8,.7,.6,.5,.4]):sample(m,node,t+i*.2,x)

class BehaviorMapTests(unittest.TestCase):
    def test_quiet_or_jitter_is_not_obstacle(self):
        for moving in (True,False,None):
            m=BehaviorMap()
            for i in range(100):sample(m,1,i*.1,.01*(i%2),moving=moving)
            self.assertFalse(m.hypotheses);self.assertFalse(m.passage)
    def test_one_turn_is_not_obstacle_and_repeated_turns_are_only_suspected(self):
        m=BehaviorMap();turnaround(m,1,0)
        self.assertEqual(len(m.hypotheses),1)
        self.assertNotIn('SUSPECTED OBSTRUCTION',[r['state'] for r in m.cells()])
        turnaround(m,2,12);turnaround(m,1,24)
        self.assertIn('SUSPECTED OBSTRUCTION',[r['state'] for r in m.cells()])
    def test_real_traversal_contradicts_hypothesis(self):
        m=BehaviorMap();turnaround(m,1,0);turnaround(m,2,12);turnaround(m,1,24)
        for i in range(16):sample(m,3,40+i*.2,i*.1)
        self.assertNotIn('SUSPECTED OBSTRUCTION',[r['state'] for r in m.cells()])
        self.assertTrue(any(r['kind']=='hypothesis_contradicted' for r in m.events))
    def test_gap_bad_quality_and_rest_do_not_create_obstacle(self):
        m=BehaviorMap()
        for i in range(10):sample(m,1,i*.2,i*.1)
        m.gap(1)
        for i in range(10):sample(m,1,10+i*.2,.9-i*.1)
        self.assertFalse(m.hypotheses)
        for i,x in enumerate([0,.2,.4,.6,.8,.6,.4,.2]):sample(m,2,20+i*.2,x,sigma=.2)
        self.assertFalse(m.hypotheses)
        for i,x in enumerate([0,.2,.4,.6,.8,.6,.4,.2]):sample(m,3,30+i*.2,x,rest=True)
        self.assertFalse(m.hypotheses)
    def test_evidence_ages_and_hysteresis_releases(self):
        m=BehaviorMap();turnaround(m,1,0);turnaround(m,2,12);turnaround(m,1,24)
        h=next(iter(m.hypotheses.values()));self.assertTrue(h['active'])
        m.advance(180);self.assertTrue(h['active'])
        self.assertLess(h['score'],m.config.activate_score)
        m.advance(700);self.assertFalse(h['active'])
        self.assertTrue(any(e['kind']=='fence_released' for e in m.events))
    def test_historical_passage_does_not_veto_new_barrier(self):
        m=BehaviorMap();m.passage[(4,0)]=dict(visits=1,nodes={3},last=0.)
        turnaround(m,1,30);turnaround(m,2,42);turnaround(m,1,54)
        self.assertTrue(any(h['active'] for h in m.hypotheses.values()))
        from automatic_fences import AutomaticFences
        f=AutomaticFences();f.update(m)
        self.assertTrue(f.exclusions);self.assertFalse(f.explored & f.exclusions)
    def test_duplicate_sample_does_not_add_evidence(self):
        m=BehaviorMap();sample(m,1,1,0);sample(m,1,1,.8)
        self.assertEqual(len(m.history[1]),1);self.assertFalse(m.passage)
    def test_config_validation(self):
        from cockroach_mapping import InferenceConfig
        with self.assertRaises(ValueError):InferenceConfig(release_score=3).validate()
        with self.assertRaises(ValueError):InferenceConfig(half_life_s=float('nan')).validate()
    def test_archive_preserves_evidence(self):
        m=BehaviorMap();turnaround(m,1,0)
        restored=BehaviorMap.load(m.document());self.assertEqual(restored.cells(),m.cells())

if __name__=='__main__':unittest.main()
