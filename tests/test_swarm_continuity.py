"""Regression coverage for filtered/raw jumps and reacquisition after an outage."""
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from swarm_core import SwarmEngine,Beacon
from swarm_localization import SwarmEKFTracker
from uwb3d_core import Config,Engine,Range
from swarm_motion import MotionReferenceSolver


class ContinuityTests(unittest.TestCase):
    def test_ambiguous_ranges_preserve_branch_without_freezing_motion(self):
        from itertools import combinations
        solver=MotionReferenceSolver((1,3,4))
        anchors={1:np.array([-1.,0.]),3:np.array([1.,0.]),4:np.array([0.,.02])}
        solver.positions=anchors.copy();solver.last[2]=np.array([0.,.5])
        for i in range(20):
            points=dict(anchors);points[2]=np.array([i*.005,.5])
            edges=[Range(a,b,float(np.linalg.norm(points[a]-points[b]))+(.04 if set((a,b))=={2,4} and i%2 else 0.),i*.04,i*.04,i,quality=1.) for a,b in combinations((1,2,3,4),2)]
            x,meta=solver.solve([1,2,3,4],edges,{},i*.04)
            self.assertNotIn(2,meta['unavailable_nodes'])
            self.assertGreater(x[1,1],.3)
        self.assertGreater(x[1,0],.06)

    def test_rejected_measurements_do_not_coast_indefinitely(self):
        tracker=SwarmEKFTracker()
        for i in range(30):tracker.update_one(2,np.array([i*.02,0.]),i*.04,.03)
        start=tracker.states[2][0][:2].copy()
        for i in range(30,60):
            tracker.update_one(2,np.array([10.*(-1)**i,10.]),i*.04,.03)
        self.assertLess(np.linalg.norm(tracker.states[2][0][:2]-start),.1)
        self.assertLess(np.linalg.norm(tracker.states[2][0][2:]),1e-6)

    def test_reference_warning_marks_live_estimate_but_does_not_hide_staleness(self):
        engine=SwarmEngine(Config(algorithm='swarm',dimensions=2,filtering=False))
        engine.last_solution={'reference_degraded':True,'reference_warning':'Reference geometry unverified'}
        for state in ('measured-supported','stale'):
            snapshot=dict(state=state,ids=[2],positions={2:[.1,.2,0.]},reason='test',
                measured_positions={},node_states={2:state},age=1.,
                metrics=dict(rms=.03,unavailable_nodes={}))
            with patch.object(Engine,'estimate',return_value=copy.deepcopy(snapshot)):
                result=engine.estimate(2.)
            self.assertEqual(result['state'],'estimated' if state=='measured-supported' else 'stale')
            self.assertTrue(result['metrics']['reference_degraded'])

    def test_reference_health_does_not_discard_fresh_mobile_observations(self):
        from itertools import combinations
        solver=MotionReferenceSolver((1,3,4))
        points={1:np.array([0.,0.]),3:np.array([1.,0.]),4:np.array([0.,1.]),2:np.array([.3,.4])}
        solver.positions={n:points[n] for n in (1,3,4)}
        for missing in (False,True):
            edges=[Range(a,b,float(np.linalg.norm(points[a]-points[b]))+(.6 if 2 not in (a,b) else 0.),1.,1.,1,quality=1.)
                   for a,b in combinations((1,2,3,4),2) if not(missing and 2 not in (a,b))]
            result,meta=solver.solve([1,2,3,4],edges,{},1.)
            self.assertTrue(meta['reference_degraded'])
            self.assertNotIn(2,meta['unavailable_nodes'])
            np.testing.assert_allclose(result[1,:2],points[2],atol=1e-5)
            # Loss of an actual mobile link remains unavailable, not disguised
            # by the conditional reference-health behavior.
            edges=[r for r in edges if set((r.a,r.b))!={1,2}]
            _,meta=solver.solve([1,2,3,4],edges,{},1.)
            self.assertIn(2,meta['unavailable_nodes'])

    def test_rest_can_reacquire_a_lagging_position_without_coasting(self):
        tracker=SwarmEKFTracker()
        for i in range(30):tracker.update_one(2,np.zeros(2),i*.04,.03,True)
        previous=np.zeros(2)
        for i in range(60):
            point=tracker.update_one(2,np.array([.8,0.]),1.2+i*.04,.03,True,max_step_dt=.04)
            self.assertLessEqual(np.linalg.norm(point-previous),.040001)
            previous=point
        self.assertLess(np.linalg.norm(previous-[.8,0.]),.03)
        self.assertLess(np.linalg.norm(tracker.states[2][0][2:]),.02)

    def test_quiet_accelerometer_does_not_freeze_constant_velocity(self):
        from itertools import combinations
        for speed in (0.,.4):
            engine=SwarmEngine(Config(algorithm='swarm',dimensions=2,filtering=False,reference_nodes=(1,3,4)))
            anchors={1:np.array([-.5,0.]),3:np.array([.5,0.]),4:np.array([0.,1.])}
            for n in (1,2,3,4):engine.registry.register(n,str(n),0.)
            engine.motion_solver=MotionReferenceSolver((1,3,4))
            engine.motion_solver.positions=anchors.copy()
            for i in range(31):
                t=i*.04;points=dict(anchors);points[2]=np.array([speed*t,.3])
                for a,b in combinations((1,2,3,4),2):
                    engine.ingest(Range(a,b,float(np.linalg.norm(points[a]-points[b])),t,t,i+1,quality=1.))
            engine.registry.nodes[2].imu=dict(sensor='LIS2DH12',moving=False,quiet_ms=1200,sample_age_ms=30,received=1.2)
            engine.estimate(1.2)
            self.assertEqual(2 in engine.stationary,speed==0.)

    def test_motion_header_is_optional_and_age_is_preserved(self):
        payload=bytearray(54);payload[:3]=b'SC\x01';payload[3:6]=bytes([1,3,1])
        self.assertIsNone(Beacon.unpack(payload,1.).motion)
        payload[36:40]=bytes([6,4,60,3])
        motion=Beacon.unpack(payload,1.).motion
        self.assertEqual(motion['sensor'],'LIS2DH12')
        self.assertTrue(motion['moving'])
        self.assertEqual(motion['quiet_ms'],1200)
        self.assertEqual(motion['sample_age_ms'],30)
        payload[36]=8
        with self.assertRaises(ValueError):Beacon.unpack(payload,1.)

    def test_near_zero_centroid_does_not_trap_trilateration(self):
        from itertools import combinations
        solver=MotionReferenceSolver((1,3,4))
        points={1:np.array([-.4077903495754393,-.3229594956016924]),
                3:np.array([.5613514090241816,-.3229594956016924]),
                4:np.array([-.1535610594487425,.6459189912033847]),
                2:np.array([.17,-.69])}
        solver.positions={n:points[n] for n in (1,3,4)}
        edges=[Range(a,b,float(np.linalg.norm(points[a]-points[b])),1.,1.,1,quality=1.)
               for a,b in combinations((1,2,3,4),2)]
        result,meta=solver.solve([1,2,3,4],edges,{},1.)
        self.assertNotIn(2,meta['unavailable_nodes'])
        np.testing.assert_allclose(result[1,:2],points[2],atol=1e-5)

    def test_failed_solution_and_partial_outage_hold_filtered_point(self):
        engine=SwarmEngine(Config(algorithm='swarm',dimensions=2,filtering=False))
        engine.ekf.update_one(2,np.array([.1,.2]),1.)
        for state,measured in [('stale',{}),('measured-supported',{})]:
            snapshot=dict(state=state,ids=[2],positions={2:[.9,-.7,0.]},
                measured_positions=measured,node_states={2:'stale'},age=1.,
                metrics=dict(rms=.03,unavailable_nodes={2:'Bad ranges'}))
            with patch.object(Engine,'estimate',return_value=copy.deepcopy(snapshot)):
                result=engine.estimate(2.)
            np.testing.assert_allclose(result['positions'][2],[.1,.2,0.])
            self.assertEqual(result['node_states'][2],'stale')

    def test_long_outage_cannot_teleport_on_first_new_observation(self):
        tracker=SwarmEKFTracker()
        tracker.update_one(2,np.array([0.,0.]),0.)
        previous=np.zeros(2)
        for i in range(1,80):
            now=5.+i*.04
            point=tracker.update_one(2,np.array([.8,0.]),now,.03,max_step_dt=.04)
            self.assertLessEqual(np.linalg.norm(point-previous),.040001)
            self.assertGreaterEqual(np.linalg.eigvalsh(tracker.states[2][1]).min(),-1e-10)
            previous=point
        self.assertLess(np.linalg.norm(previous-[.8,0.]),.05)

    def test_slow_motion_is_not_frozen_and_single_large_outlier_is_rejected(self):
        tracker=SwarmEKFTracker();errors=[]
        for i in range(200):
            t=i*.04;truth=np.array([max(0.,t-2.)*.5,0.])
            observed=truth+np.array([.01*np.sin(i),.01*np.cos(i)])
            if i==100:observed+=5.
            point=tracker.update_one(2,observed,t,.03,t<2.)
            if i>60:errors.append(np.linalg.norm(point-truth))
        self.assertLess(max(errors),.15)
        self.assertGreater(tracker.rejected,0)


if __name__=='__main__':unittest.main()
