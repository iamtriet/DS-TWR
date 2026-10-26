import sys
from pathlib import Path
from types import SimpleNamespace
from itertools import combinations
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from swarm_cooperative_tracker import CooperativeRangeTracker
from swarm_core import Beacon
from uwb3d_core import Range


class CooperativeTests(unittest.TestCase):
    def test_measured_collector_origin_and_quiet_covariance_remain_bounded(self):
        from collections import defaultdict,deque
        tracker=CooperativeRangeTracker(origin=6);ids=list(range(1,7))
        history=defaultdict(lambda:deque(maxlen=80));rng=np.random.default_rng(19)
        initial=np.array([[-.8,-.5],[.3,-.3],[.7,.8],[-.7,.8],[0.,.2],[1.2,-.9]])
        final_errors=[]
        for step in range(400):
            now=step*.04;truth=initial.copy();truth[1,0]+=min(max(now-3.,0)*.2,.6)
            moving=3<=now<6
            d=np.zeros((6,6));edges=[]
            for i,j in combinations(range(6),2):
                distance=np.linalg.norm(truth[i]-truth[j])+rng.normal(0,.015)
                # Imperfect startup must be corrected after rest detection.
                if step<10 and (i,j)==(0,2):distance+=.25
                d[i,j]=d[j,i]=distance
                edges.append(Range(i+1,j+1,distance,now,now,step,quality=1.))
                history[(i+1,j+1)].append((now,distance))
            nodes={i:SimpleNamespace(imu=dict(sensor='BNO085',moving=i==2 and moving,quiet_ms=0 if i==2 and moving else 5100,score_mg=30 if i==2 and moving else 0,sample_age_ms=20,received=now)) for i in ids}
            points,meta=tracker.solve(ids,d,edges,nodes,history,now)
            np.testing.assert_allclose(points[-1],0.,atol=1e-12)
            self.assertGreaterEqual(np.linalg.eigvalsh(tracker.p).min(),-1e-9)
            if step>350:
                final_errors.append(np.sqrt(np.mean([(np.linalg.norm(points[i]-points[j])-np.linalg.norm(truth[i]-truth[j]))**2 for i,j in combinations(range(6),2)])))
        self.assertLess(np.mean(final_errors),.035)
        self.assertLess(meta['uncertainty'],.3)
        with self.assertRaisesRegex(ValueError,'Origin collector is missing'):
            tracker.solve(ids[:-1],d[:-1,:-1],edges,nodes,history,now)

    def test_quiet_group_survives_bad_range_history_but_releases_motion_and_stale_imu(self):
        tracker=CooperativeRangeTracker();ids=list(range(1,6))
        truth=np.array([[-.8,-.5],[.3,-.3],[.7,.8],[-.7,.8],[0.,.2]])
        d=np.linalg.norm(truth[:,None,:]-truth[None,:,:],axis=2)
        nodes={i:SimpleNamespace(imu=dict(sensor='BNO085',moving=False,quiet_ms=5100,score_mg=0,sample_age_ms=20,received=0.)) for i in ids}
        tracker.rest_members=set(ids)
        edges=[Range(i,j,d[i-1,j-1],0.,0.,0,quality=1.) for i,j in combinations(ids,2)]
        tracker.solve(ids,d,edges,nodes,{},0.)
        self.assertEqual(tracker.rest_members,set(ids))
        nodes[2].imu.update(moving=True,quiet_ms=0)
        _,meta=tracker.solve(ids,d,edges,nodes,{},.04)
        self.assertEqual(meta['stationary'],[1,3,4,5])
        nodes[2].imu.update(moving=False,quiet_ms=5100)
        tracker.solve(ids,d,edges,nodes,{},.08)
        self.assertNotIn(2,tracker.rest_members)
        tracker.solve(ids,d,edges,nodes,{},1.)
        self.assertEqual(tracker.rest_members,set())

    def test_one_mobile_does_not_drag_quiet_peers_and_releases_when_they_move(self):
        from collections import defaultdict,deque
        tracker=CooperativeRangeTracker();ids=list(range(1,6));history=defaultdict(lambda:deque(maxlen=80))
        initial=np.array([[-.8,-.5],[.3,-.3],[.7,.8],[-.7,.8],[0.,.2]])
        baseline=None;max_static=0.
        for step in range(130):
            now=step*.04;truth=initial.copy();truth[1,0]+=min(max(now-1.,0)*.2,.6)
            d=np.zeros((5,5));edges=[]
            for i,j in combinations(range(5),2):
                distance=np.linalg.norm(truth[i]-truth[j])
                if step>24 and 1 in (i,j):distance+=.08*np.sin(step*.7+i+j)
                # Moving a board/hand also perturbs other RF paths. Quiet
                # peers must not chase those ranges during confirmed motion.
                if step>24 and (i,j)==(2,3):distance+=.12*np.sin(step*.3)
                d[i,j]=d[j,i]=distance
                edges.append(Range(i+1,j+1,distance,now,now,step,quality=1.))
                history[(i+1,j+1)].append((now,distance))
            nodes={i:SimpleNamespace(imu=dict(sensor='BNO085',moving=i==2 and now>=1,quiet_ms=0 if i==2 and now>=1 else 5100,score_mg=30 if i==2 else 0,sample_age_ms=20,received=now)) for i in ids}
            points,meta=tracker.solve(ids,d,edges,nodes,history,now)
            if step==24:baseline=points.copy()
            if step>24:max_static=max(max_static,float(np.max(np.linalg.norm(points[[0,2,3,4]]-baseline[[0,2,3,4]],axis=1))))
        self.assertLess(max_static,.001)
        self.assertGreater(np.linalg.norm(points[1]-baseline[1]),.5)
        mobile_range_error=[np.linalg.norm(points[1]-points[i])-np.linalg.norm(truth[1]-truth[i]) for i in (0,2,3,4)]
        self.assertLess(np.sqrt(np.mean(np.square(mobile_range_error))),.06)
        self.assertEqual(meta['frame_nodes'],[1,3,4,5])
        for node in nodes.values():node.imu.update(moving=True,quiet_ms=0)
        _,meta=tracker.solve(ids,d,edges,nodes,history,now)
        self.assertEqual(meta['frame_nodes'],[])

    def test_frame_alignment_preserves_all_distances(self):
        from swarm_cooperative_tracker import MovingFrameAligner
        frame=MovingFrameAligner();ids=[1,2,3,4]
        points=np.array([[0.,0.],[1.,0.],[0.,1.],[.3,.4]])
        frame.align(ids,points,{1,2,3})
        moved=points.copy();moved[3]+=[.5,.2];moved-=moved.mean(0)
        shown,used=frame.align(ids,moved,{1,2,3})
        np.testing.assert_allclose(shown[:3],points[:3],atol=1e-12)
        for i,j in combinations(range(4),2):self.assertAlmostEqual(np.linalg.norm(shown[i]-shown[j]),np.linalg.norm(moved[i]-moved[j]))
        self.assertGreater(np.linalg.det(frame.rotation),0.)

    def test_all_nodes_move_with_no_fixed_reference(self):
        tracker=CooperativeRangeTracker();ids=list(range(1,6));rng=np.random.default_rng(84)
        initial=np.array([[-.8,-.5],[.8,-.5],[.7,.8],[-.7,.8],[0.,.2]])
        errors=[];first=None
        for step in range(150):
            now=step*.04
            truth=initial+np.c_[.15*np.sin(now+np.arange(5)),.15*np.cos(now*.7+np.arange(5))]
            # Unobservable common translation must not distort relative shape.
            truth+=np.array([now*.3,0.])
            d=np.zeros((5,5));edges=[]
            for i,j in combinations(range(5),2):
                distance=np.linalg.norm(truth[i]-truth[j])+rng.normal(0,.01)
                if step==60 and (i,j)==(0,1):distance+=.8
                d[i,j]=d[j,i]=distance
                edges.append(Range(i+1,j+1,distance,now,now,step,quality=1.))
            nodes={i:SimpleNamespace(imu=dict(sensor='BNO085',moving=True,quiet_ms=0,score_mg=30,sample_age_ms=20,received=now)) for i in ids}
            points,meta=tracker.solve(ids,d,edges,nodes,{},now)
            if first is None:first=points.copy()
            np.testing.assert_allclose(points.mean(0),0.,atol=1e-9)
            errors.append(np.sqrt(np.mean([(np.linalg.norm(points[i]-points[j])-np.linalg.norm(truth[i]-truth[j]))**2 for i,j in combinations(range(5),2)])))
            self.assertTrue(all(meta['node_quality'][i]['bno_fresh'] for i in ids))
            self.assertGreaterEqual(np.linalg.eigvalsh(tracker.p).min(),-1e-9)
        self.assertLess(np.mean(errors),.05)
        self.assertTrue(all(np.linalg.norm(points[i]-first[i])>.04 for i in range(5)))

    def test_bno_wire_source_is_explicit(self):
        p=bytearray(54);p[:3]=b'SC\x01';p[3:6]=bytes([1,3,1]);p[36:40]=bytes([10,20,50,2])
        motion=Beacon.unpack(p,1.).motion
        self.assertEqual(motion['sensor'],'BNO085')
        self.assertEqual(motion['score_mg'],20)
        p[36]=8
        with self.assertRaises(ValueError):Beacon.unpack(p,1.)


if __name__=='__main__':unittest.main()
