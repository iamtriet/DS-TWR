import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import itertools
import math
import unittest
from auto_anchor import relative_layout, AutoAnchorEstimate, AutoAnchorFleet


COORDS = [[0,0,0],[3,0,0],[.2,2,0],[.5,.3,1.5]]
PLAN = dict(anchors=[dict(id=f'A{i+1:03X}',name=f'a{i}',address=f'a{i}') for i in range(4)],
            tags=[dict(id='B001',name='tag',address='tag')])


def epoch(coords=COORDS, target=(1,.7,.5)):
    edges = [[math.dist(a,b),10.] for a,b in itertools.combinations(coords,2)]
    return dict(start=10.,end=12.,before=edges,after=[v[:] for v in edges],
                tags=[[a['id'],'B001',math.dist(p,target),11.+i*.1] for i,(a,p) in enumerate(zip(PLAN['anchors'],coords))])


class AutoGeometryTests(unittest.TestCase):
    def test_early_tag_fix_does_not_extend_anchor_validity(self):
        now=[12.];model=AutoAnchorEstimate(PLAN,clock=lambda:now[0])
        model.accept(epoch()); model.begin(11.)
        e=epoch(target=(1.2,.8,.6));rows=e['tags']
        for row in rows: row[3]+=1.
        now[0]=13.
        self.assertTrue(model.update_tag(rows))
        self.assertLess(math.dist(model.display().locate('B001')[0],[1.2,.8,.6]),1e-8)
        now[0]=16.
        for row in rows: row[3]+=3.
        self.assertFalse(model.update_tag(rows))
        self.assertTrue(model.display().held)

    def test_motion_in_reused_survey_is_not_forgotten(self):
        model=AutoAnchorEstimate(PLAN,clock=lambda:12.)
        model.feed('a0','MOTION,1000,1,3,1.0,0.0',11.)
        model.begin(10.)
        model.accept(epoch())
        self.assertIsNone(model.profile)
        model.begin(11.5)
        model.accept(epoch())
        self.assertIsNotNone(model.profile)

    def test_display_holds_snapshot_but_live_solver_stays_invalid(self):
        now=[12.]; model=AutoAnchorEstimate(PLAN,clock=lambda:now[0])
        self.assertIs(model.display(),model)
        model.accept(epoch())
        original=model.display()
        self.assertFalse(original.held)
        expected=original.locate('B001')
        bad=epoch();bad['before'][0][0]=None
        model.accept(bad)
        with self.assertRaises(ValueError): model.locate('B001')
        self.assertIs(model.display(),original)
        self.assertTrue(original.held)
        self.assertEqual(original.locate('B001'),expected)
        now[0]=42.
        self.assertEqual(model.display().age,32.)
        model.accept(epoch())
        self.assertTrue(model.display().held)  # An already stale epoch is not live.

    def test_partial_new_layout_does_not_mix_old_tag_frame(self):
        model=AutoAnchorEstimate(PLAN,clock=lambda:12.)
        model.accept(epoch()); original=model.display()
        moved=[p[:] for p in COORDS];moved[3]=[.8,.2,1.7]
        partial=epoch(moved);partial['tags'][0][2]=None
        model.accept(partial)
        self.assertIs(model.display(),original)
        self.assertTrue(original.held)
        model.accept(epoch(moved))
        self.assertIsNot(model.display(),original)
        self.assertFalse(model.display().held)
        self.assertLess(math.dist(model.display().profile['anchors']['a3'],moved[3]),1e-8)

    def test_explicit_planar_mode_and_shift(self):
        coords=[[0,0,0],[3,0,0],[.2,2,0],[3,2,0]]
        model=AutoAnchorEstimate(PLAN,clock=lambda:12.,mode='xy')
        for fourth in ([3,2,0],[2.5,2.3,0]):
            coords[3]=fourth
            model.accept(epoch(coords,(1,.7,0)))
            self.assertLess(math.dist(model.locate('B001')[0],[1,.7,0]),1e-8)
            self.assertLess(math.dist(model.profile['anchors']['a3'],fourth),1e-8)

    def test_planar_mode_rejects_nonplanar_ranges(self):
        e=epoch()
        with self.assertRaises(ValueError): relative_layout([v[0] for v in e['before']], 'xy')

    def test_planar_noise_on_collinear_subset_uses_all_edges(self):
        coords=[[0,0,0],[1,0,0],[.2,1,0],[2,0,0]]
        distances=[math.dist(a,b) for a,b in itertools.combinations(coords,2)]
        distances[2]+=.02
        fitted=relative_layout(distances,'xy')
        self.assertLess(max(abs(math.dist(a,b)-d) for (a,b),d in zip(itertools.combinations(fitted,2),distances)),.05)
        distances[2]+=1.
        with self.assertRaises(ValueError): relative_layout(distances,'xy')

    def test_exact_relative_geometry_and_tag(self):
        model = AutoAnchorEstimate(PLAN,clock=lambda:12.)
        model.accept(epoch())
        xyz,residual = model.locate('B001')
        self.assertLess(math.dist(xyz,[1,.7,.5]),1e-8)
        self.assertLess(residual,1e-8)

    def test_shift_recalculates_without_manual_input(self):
        model = AutoAnchorEstimate(PLAN,clock=lambda:12.)
        model.accept(epoch())
        moved = [p[:] for p in COORDS]; moved[3]=[.8,.2,1.7]
        model.accept(epoch(moved))
        self.assertLess(math.dist(model.profile['anchors']['a3'],moved[3]),1e-8)
        self.assertLess(math.dist(model.locate('B001')[0],[1,.7,.5]),1e-8)

    def test_reject_flat_impossible_and_nonfinite(self):
        for values in ([1,1,1,3,1,1], [float('nan')]*6,
                       [math.dist(a,b) for a,b in itertools.combinations([[0,0,0],[2,0,0],[0,2,0],[2,2,0]],2)]):
            with self.assertRaises(ValueError): relative_layout(values)

    def test_reject_changed_missing_and_slow_snapshots(self):
        for case in ('changed','missing','slow'):
            model=AutoAnchorEstimate(PLAN,clock=lambda:12.)
            model.accept(epoch()); e=epoch()
            if case=='changed': e['after'][0][0]+=.1
            if case=='missing': e['before'][0][0]=None
            if case=='slow': e['end']=16.
            model.accept(e)
            self.assertIsNone(model.profile)
            with self.assertRaises(ValueError): model.locate('B001')

    def test_expiry_and_motion_clear_positions(self):
        now=[12.]; model=AutoAnchorEstimate(PLAN,clock=lambda:now[0])
        model.accept(epoch()); now[0]=16.
        with self.assertRaises(ValueError): model.locate('B001')
        now[0]=12.; model.accept(epoch()); model.begin()
        model.feed('a0','MOTION,1000,1,3,1.0,0.0',12.)
        model.accept(epoch())
        self.assertIsNone(model.profile)
        model.begin(); model.accept(epoch())
        self.assertIsNotNone(model.profile)

    def test_partial_tag_does_not_reuse_previous_epoch(self):
        model=AutoAnchorEstimate(PLAN,clock=lambda:12.)
        model.accept(epoch()); e=epoch(); e['tags'][0][2]=None
        model.accept(e)
        with self.assertRaises(ValueError): model.locate('B001')


class AutoScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_roles_listener_allowlists_and_cleanup(self):
        from types import SimpleNamespace
        calls=[]
        class Session:
            def __init__(self,label,emit,address):
                self.label=label; self.closed=self.desynchronized=False
                self.client=SimpleNamespace(is_connected=True)
            async def open(self): pass
            async def close(self): self.closed=True
            async def command(self,cmd):
                calls.append((self.label,cmd))
                if cmd=='uwb status': return ['UWB role=tag id=0x0000 peer=0x0000']
                return []
        fleet=AutoAnchorFleet(PLAN,lambda *e:None,Session)
        await fleet.start(False)
        for a in PLAN['anchors']:
            self.assertIn((a['name'],'uwb listen'),calls)
            self.assertIn((a['name'],'uwb role anchor'),calls)
            allowed='uwb anchors '+' '.join(b['id'] for b in PLAN['anchors'] if b!=a)
            self.assertIn((a['name'],allowed),calls)
        self.assertNotIn(('tag','uwb listen'),calls)
        fleet.connections['tag'].client.is_connected=False
        await fleet.ensure_connections()
        self.assertEqual(len(fleet.started),5)
        self.assertEqual(sum(n=='tag' and c=='uwb continuous' for n,c in calls),2)
        calls.clear()
        self.assertEqual(await fleet.stop(),[])
        self.assertEqual(sum(c=='uwb stop' for _,c in calls),5)

    async def test_brackets_tags_with_six_reciprocal_edges(self):
        events=[]; calls=[]
        fleet=AutoAnchorFleet(PLAN,lambda *e:events.append(e))
        async def ensure(): pass
        fleet.ensure_connections=ensure
        async def measure(a,b): calls.append((a['id'],b['id'])); return 1.,0.
        fleet.measure=measure
        await fleet.cycle()
        self.assertEqual(len(calls),16)
        self.assertEqual(calls[-6:],[(b,a) for a,b in calls[:6]])
        self.assertTrue(all(b=='B001' for a,b in calls[6:10]))
        self.assertEqual([e[0] for e in events],['auto_begin','auto_tag','auto_epoch'])
        calls.clear();events.clear()
        await fleet.cycle()
        self.assertEqual(len(calls),10)  # Four tag links plus six closing links.
        self.assertEqual(calls[-6:],[(a['id'],b['id']) for a,b in itertools.combinations(PLAN['anchors'],2)])


if __name__=='__main__': unittest.main()
