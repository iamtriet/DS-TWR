import itertools
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from cooperative_tracking import CooperativeGraph,CooperativeDisplay,CooperativeFleet,Link,edge,fit_graph

PLAN=dict(anchors=[dict(id=f'A{i+1:03X}',name=f'a{i}',address=f'a{i}') for i in range(4)],
          tags=[dict(id=f'B{i+1:03X}',name=f't{i}',address=f't{i}') for i in range(2)])
POINTS={'A001':[0,0,0],'A002':[3,0,0],'A003':[.2,2,0],'A004':[3,2,0],
        'B001':[1,.7,0],'B002':[1.8,1.4,0]}


def populate(graph,points=POINTS,stamp=10.):
    for a,b in graph.choose():graph.update(a,b,math.dist(points[a],points[b]),stamp)


class GraphTests(unittest.TestCase):
    def test_reseed_recovers_real_neighbor_change_local_minimum(self):
        import json
        case=json.loads(Path(__file__).with_name('cooperative_reseed_case.json').read_text())
        graph=CooperativeGraph(case['plan'],clock=lambda:10.5)
        graph.selected={tuple(k) for k in case['selected']}
        graph.positions=case['positions']
        for e in case['edges']:
            if e['distance'] is not None:graph.update(e['a'],e['b'],e['distance'],10.)
        measurements=[(k,graph.links[k].samples[-1][0],.05,graph.links[k].reliability,10.) for k in sorted(graph.selected)]
        with self.assertRaises(ValueError):
            fit_graph(graph.ids,measurements,2,[graph.positions[n][:2] for n in graph.ids])
        result=graph.solve()
        self.assertTrue(result['valid'],result['reason'])
        self.assertLess(result['frame']['rms'],.04)

    def test_schedule_refreshes_edges_due_before_cycle_end(self):
        graph=CooperativeGraph(PLAN,clock=lambda:13.5);populate(graph)
        graph.cycle_durations.append(1.2)
        slots=graph.schedule()
        self.assertEqual(sum(a.startswith('A') and b.startswith('A') for a,b in slots),6)

    def test_sparse_cooperative_graph_solves_all_nodes(self):
        graph=CooperativeGraph(PLAN,clock=lambda:10.5)
        populate(graph);result=graph.solve()
        self.assertTrue(result['valid'],result['reason'])
        self.assertIn(edge('B001','B002'),graph.selected)
        self.assertLess(len(graph.selected),15)
        for node,p in POINTS.items():self.assertLess(math.dist(graph.positions[node],p),1e-6)

    def test_tag_edge_is_used_not_just_displayed(self):
        graph=CooperativeGraph(PLAN,clock=lambda:10.5);populate(graph)
        graph.links.pop(edge('B001','B002'))
        self.assertFalse(graph.solve()['valid'])

    def test_dynamic_selection_replaces_failed_peer_and_probes(self):
        graph=CooperativeGraph(PLAN,clock=lambda:10.5);populate(graph);graph.solve()
        graph.choose();first=set(graph.neighbors['B001'])
        bad=next(iter(first));link=graph.links[edge('B001',bad)]
        for i in range(15):link.update(None,10.+i*.01)
        graph.choose()
        self.assertNotIn(bad,graph.neighbors['B001'])
        graph.round=0
        self.assertTrue(set(graph.schedule())-graph.selected)

    def test_stale_and_motion_force_remeasure(self):
        now=[10.5];graph=CooperativeGraph(PLAN,clock=lambda:now[0]);populate(graph)
        self.assertTrue(graph.solve()['valid'])
        now[0]=14.
        self.assertFalse(graph.solve()['valid'])
        now[0]=11.;graph.feed('a0','MOTION,1,1,3,0.7,0.0',11.)
        self.assertFalse(graph.solve()['valid'])
        self.assertEqual(sum(a.startswith('A') and b.startswith('A') for a,b in graph.schedule()),6)

    def test_degenerate_disconnected_and_outlier_graph_rejected(self):
        graph=CooperativeGraph(PLAN,clock=lambda:10.5);populate(graph)
        graph.links[edge('A001','A002')].samples[-1]=(10.,10.)
        self.assertFalse(graph.solve()['valid'])
        graph=CooperativeGraph(PLAN,clock=lambda:10.5)
        points={n:[i,0.,0.] for i,n in enumerate(graph.ids)}
        populate(graph,points)
        self.assertFalse(graph.solve()['valid'])
        with self.assertRaises(ValueError):fit_graph(graph.ids,[],2,[[p[0],p[1]] for p in points.values()])

    def test_noisy_joint_fit_and_expiring_display(self):
        now=[10.5];graph=CooperativeGraph(PLAN,clock=lambda:now[0]);populate(graph)
        for i,link in enumerate(graph.links.values()):
            d,t=link.samples[-1];link.samples[-1]=(d+(.01 if i%2 else -.01),t)
        result=graph.solve();self.assertTrue(result['valid'],result['reason'])
        display=CooperativeDisplay(PLAN,clock=lambda:now[0]);display.accept(result)
        self.assertFalse(display.display().held)
        now[0]=20.
        self.assertTrue(display.display().held)
        self.assertIn('B002',display.display().results)

    def test_mobile_anchor_recomputed(self):
        graph=CooperativeGraph(PLAN,clock=lambda:10.5);populate(graph);graph.solve()
        moved=dict(POINTS,A004=[2.8,2.3,0]);populate(graph,moved)
        result=graph.solve();self.assertTrue(result['valid'],result['reason'])
        self.assertLess(math.dist(graph.positions['A004'],moved['A004']),1e-4)

    def test_3d_sparse_graph(self):
        graph=CooperativeGraph(PLAN,mode='xyz',clock=lambda:10.5)
        points=dict(POINTS,A004=[.5,.3,1.5],B001=[1,.7,.5],B002=[1.8,1.4,.6])
        populate(graph,points);result=graph.solve()
        self.assertTrue(result['valid'],result['reason'])
        for n,p in points.items():self.assertLess(math.dist(graph.positions[n],p),1e-5)


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def test_slots_serial_and_include_real_tag_pair(self):
        events=[];fleet=CooperativeFleet(PLAN,lambda k,v:events.append((k,v)))
        active=0;peak=0
        async def ensure():pass
        async def measure(a,b):
            nonlocal active,peak
            import asyncio,time
            active+=1;peak=max(peak,active);await asyncio.sleep(0)
            active-=1
            return math.dist(POINTS[a['id']],POINTS[b['id']]),time.monotonic()
        fleet.ensure_connections=ensure;fleet.measure=measure
        await fleet.cycle();initial=[v for k,v in events if k=='coop_slot']
        self.assertEqual(peak,1)
        self.assertTrue(any(v['a']=='B001' and v['b']=='B002' for v in initial))
        events.clear();await fleet.cycle()
        steady=[v for k,v in events if k=='coop_slot']
        self.assertLess(len(steady),len(initial))
        self.assertTrue([v for k,v in events if k=='coop_frame'][0]['valid'])


if __name__=='__main__':unittest.main()
