import asyncio
import itertools
import json
import math
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from uwb3d_protocol import *
from uwb3d_core import Config,Engine,Range,align,mds,sparse_edges
from uwb3d_runtime import Runtime,Simulator,Recorder,export_csv


class ProtocolTests(unittest.TestCase):
    def test_fragment_concatenate_crc_version_bounds_and_resync(self):
        raw=encode(RANGE,1,12,range_payload(1,2,1.23,1,10));d=Decoder()
        self.assertEqual(d.feed(raw[:4]),[])
        self.assertEqual(decode_range(d.feed(raw[4:])[0].payload)['distance'],1.23)
        bad=bytearray(raw);bad[-4]^=3
        self.assertEqual(d.feed(bad),[])
        self.assertEqual(len(d.feed(encode(HEARTBEAT,2,12,b'\1\0')+encode(HEARTBEAT,3,13,b'\1\0'))),2)
        self.assertEqual(d.feed(b'X'*5000+b'\0'),[])
        self.assertEqual(len(d.feed(encode(HEARTBEAT,4,14,b'\1\0'))),1)
        data=bytearray(cobs_decode(raw[:-1]));data[0]=2
        self.assertEqual(d.feed(cobs_encode(data)+b'\0'),[])
        with self.assertRaises(ValueError):range_payload(0,2,1,1,0)
        with self.assertRaises(ValueError):decode_range(b'\0'*23)
        with self.assertRaises(ValueError):encode(1,0,0,b'a'*4097)
    def test_sequence_wrap_duplicate_and_loss(self):
        d=Decoder()
        for seq in (0xfffffffe,0xffffffff,0,2):self.assertEqual(len(d.feed(encode(HEARTBEAT,seq,0))),1)
        self.assertEqual(d.feed(encode(HEARTBEAT,2,0)),[])
        self.assertEqual(d.feed(encode(HEARTBEAT,1,0)),[])
        self.assertEqual(d.stats['dropped'],1);self.assertEqual(d.stats['duplicate'],1)
    def test_gateway_reboot_resets_sequence(self):
        d=Decoder();d.feed(encode(HEARTBEAT,900,100000000,b'\1\0'))
        packets=d.feed(encode(DISCOVERY,0,10000,DISCOVERY_BODY.pack(1,123,1,0,14,1)))
        self.assertTrue(packets[0].reset);self.assertEqual(d.stats['resets'],1)
    def test_ds_clocks_drift_and_rollover(self):
        distance=12.;flight=distance/C/DTU;poll=MASK40-10000000;response=poll+60000000;final=poll+6000000000
        for drift in (0,20e-6,-25e-6):
            i=lambda t:round(t)&MASK40
            j=lambda t:round((t-poll)*(1+drift)+123456)&MASK40
            measured=ds_distance(i(poll),j(poll+flight),j(response),i(response+flight),i(final),j(final+flight))
            self.assertLess(abs(measured-distance),.02)
        with self.assertRaises(ValueError):ds_distance(1,2,3,0,4,5)
    def test_pipeline_sequence_and_missing_timestamp(self):
        i,j,k=1,2,0xffffffff;t0=10000000;delay=60000000;tof=1500
        poll=Broadcast(i,k,t0,{})
        response=Broadcast(j,k,t0+delay,{(i,k):t0+tof})
        final=Broadcast(i,0,t0+2*delay,{(j,k):t0+delay+tof})
        report=Broadcast(j,0,t0+3*delay,{(i,0):t0+2*delay+tof})
        ledger=BroadcastLedger()
        for b in (poll,response,final):self.assertEqual(ledger.add(Broadcast.unpack(b.pack())),[])
        result=ledger.add(report);self.assertEqual(len(result),1)
        self.assertAlmostEqual(result[0][3],tof*DTU*C,places=8)
        self.assertEqual(ledger.add(report),[])
    def test_schedule_not_misreported_as_qualified(self):
        budget=timing_budget();self.assertTrue(budget['arithmetic_fits'])
        self.assertTrue(budget['current_radio_frame_fits']);self.assertFalse(budget['hardware_qualified'])
        self.assertFalse(budget['firmware_schedule_supported'])
        self.assertEqual(budget['payload_bytes'],427)
        self.assertTrue(timing_budget(slot_ms=10,superframe_ms=500)['firmware_schedule_supported'])


def make_engine(n=6,noise=0.,sparse=False,flat=False):
    rng=np.random.default_rng(22);points=rng.uniform(-3,3,(n,3))
    if flat:points[:,2]=0
    engine=Engine(Config(solver_budget_ms=500,max_evaluations=80,filtering=False,temporal_weight=0))
    for i in range(n):engine.registry.register(i+1,str(i+1),10.)
    pairs=sparse_edges(list(range(1,n+1))) if sparse else itertools.combinations(range(1,n+1),2)
    for a,b in pairs:engine.ingest(Range(a,b,float(np.linalg.norm(points[a-1]-points[b-1])+rng.normal(0,noise)),10.,10.,1))
    return engine,points


class LocalizationTests(unittest.TestCase):
    def test_explicit_tabletop_mode_estimates_xy_and_labels_assumed_height(self):
        engine,truth=make_engine(6,noise=.005,flat=True)
        engine.config.dimensions=2;engine.config.solver_budget_ms=500
        state=engine.estimate(10.01)
        self.assertTrue(state['positions'],state['reason'])
        self.assertEqual(state['dimensions'],2)
        self.assertIn('assumed table plane',state['reason'])
        p=np.array(list(state['positions'].values()))
        self.assertTrue(np.all(p[:,2]==0))
        fitted,_=align(p,truth);self.assertLess(np.sqrt(np.mean((fitted-truth)**2)),.02)
    def test_tabletop_mode_rejects_collinear_and_incomplete_bootstrap(self):
        engine,_=make_engine(6,flat=True);engine.config.dimensions=2
        del engine.matrix.links[(1,6)]
        self.assertFalse(engine.estimate(10.01)['positions'])
        with self.assertRaises(ValueError):mds(np.abs(np.arange(6)[:,None]-np.arange(6)[None,:]),2)
    def test_noisy_coplanar_does_not_invent_confident_z(self):
        for n in (6,10,20,30):
            engine,_=make_engine(n,noise=.02,flat=True)
            self.assertFalse(engine.estimate(10.01)['positions'])
    def test_full_6_10_20_30_nodes(self):
        for n in (6,10,20,30):
            with self.subTest(n=n):
                engine,truth=make_engine(n,noise=.005);state=engine.estimate(10.01)
                self.assertTrue(state['positions'],state['reason'])
                fitted,_=align(np.array(list(state['positions'].values())),truth)
                self.assertLess(np.sqrt(np.mean((fitted-truth)**2)),.015)
    def test_sparse_30_nodes(self):
        engine,truth=make_engine(30,sparse=True);state=engine.estimate(10.01)
        self.assertTrue(state['positions'],state['reason'])
        fitted,_=align(np.array(list(state['positions'].values())),truth)
        self.assertLess(np.sqrt(np.mean((fitted-truth)**2)),.03)
    def test_two_nodes_flat_disconnected_missing_and_stale(self):
        for n,flat in ((2,False),(6,True)):
            engine,_=make_engine(n,flat=flat);self.assertFalse(engine.estimate(10.01)['positions'])
        engine,_=make_engine(10)
        engine.matrix.links={k:v for k,v in engine.matrix.links.items() if (k[0]<=5)==(k[1]<=5)}
        self.assertFalse(engine.estimate(10.01)['positions'])
        engine,_=make_engine(6);self.assertTrue(engine.estimate(10.01)['positions'])
        self.assertEqual(engine.estimate(11.)['state'],'stale')
        self.assertEqual(engine.estimate(20.)['state'],'unavailable')
        with self.assertRaises(ValueError):mds(np.array([[0,np.nan],[np.nan,0]]))
    def test_translation_and_reflection_are_not_absolute_motion(self):
        engine,truth=make_engine(10);first=engine.estimate(10.01)
        translated=truth+np.array([100,40,-20])
        for r in list(engine.matrix.links.values()):
            engine.ingest(Range(r.a,r.b,float(np.linalg.norm(translated[r.a-1]-translated[r.b-1])),10.1,10.1,2))
        second=engine.estimate(10.11)
        self.assertLess(np.max(np.abs(np.array(list(second['positions'].values()))-np.array(list(first['positions'].values())))),.001)
        reflected=truth*np.array([-1,1,1]);fitted,reflection=align(reflected,truth)
        self.assertTrue(reflection);self.assertLess(np.max(np.abs(fitted-truth)),1e-8)
    def test_registry_duplicate_and_matrix_invalid(self):
        engine,_=make_engine(6)
        with self.assertRaises(ValueError):engine.registry.register(1,'other',10)
        with self.assertRaises(ValueError):engine.registry.register(7,'1',10)
        with self.assertRaises(ValueError):engine.ingest(Range(1,2,-1,10.1,10.1,2))
        with self.assertRaises(ValueError):engine.ingest(Range(1,2,1,11,10,2))
        self.assertFalse(engine.ingest(Range(1,2,1,10,10,1)))
        d,age,edges=engine.matrix.arrays(list(range(1,7)),10.01)
        np.testing.assert_allclose(d,d.T);self.assertTrue(np.all(np.diag(d)==0))
    def test_prediction_age_limit(self):
        engine,_=make_engine(6);engine.config.filtering=True;engine.estimate(10.01)
        self.assertEqual(engine.estimate(10.1)['state'],'predicted')
        self.assertEqual(engine.estimate(10.6)['state'],'stale')


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_uart_drains_partial_blocks_without_waiting_for_fill(self):
        from unittest.mock import patch
        reads=[];closed=[]
        payload=encode(DISCOVERY,0,1000000,DISCOVERY_BODY.pack(1,123,1,0,18,0))
        class Port:
            def __init__(self,port,baud,timeout):
                if timeout!=0:raise AssertionError('UART must drain without waiting for a full block')
            def read(self,size):reads.append(size);return payload if len(reads)==1 else b''
            def close(self):closed.append(True)
        runtime=Runtime(asyncio.get_running_loop())
        with patch('serial.Serial',Port):
            try:
                await runtime.start('uart',port='TEST')
                await asyncio.wait_for(runtime.uart_ready.wait(),1)
                for _ in range(30):
                    if runtime.snapshot().get('nodes'):break
                    await asyncio.sleep(.02)
                self.assertEqual(runtime.snapshot()['ids'],[1])
                self.assertLess(len(reads),200)
            finally:await runtime.shutdown()
        self.assertTrue(closed)
    async def test_duplicate_ranges_do_not_inflate_measurement_rate(self):
        runtime=Runtime(asyncio.get_running_loop())
        try:
            for n in (1,2):runtime.engine.registry.register(n,str(n),10)
            body=range_payload(1,2,1.25,7,1000000)
            runtime.submit(encode(RANGE,1,1000000,body)+encode(RANGE,2,1000000,body),10)
            runtime._process()
            self.assertEqual(len(runtime.ranges),1)
        finally:await runtime.shutdown()
    async def test_six_node_bootstrap_probes_without_a_prior_position(self):
        runtime=Runtime(asyncio.get_running_loop())
        try:
            runtime.source='gateway';runtime.network=dict(period_us=100000)
            for id in range(1,7):runtime.engine.registry.register(id,str(id),time.monotonic())
            masks=[]
            for _ in range(5):
                runtime.last_selection=0;state=runtime._step()
                self.assertFalse(state['positions'])
                command=runtime.outgoing.pop();self.assertTrue(command.startswith('net assign 6 '))
                masks.append(int(command.split()[-1],16))
            self.assertEqual(len(set(masks)),5)
            self.assertTrue(all(m.bit_count()==4 and 0<m<32 for m in masks))
            self.assertEqual(set().union(*({i+1 for i in range(5) if m&(1<<i)} for m in masks)),set(range(1,6)))
        finally:await runtime.shutdown()
    async def test_scenarios_use_ranges_and_degrade_on_bad_geometry(self):
        runtime=Runtime(asyncio.get_running_loop(),Config(solver_budget_ms=500))
        try:
            for scenario in ('Rapid motion','Packet loss','Missing links','Disconnected','Coplanar','Join/leave','NLOS bias'):
                runtime.engine=Engine(runtime.config);runtime.decoder=Decoder();runtime.offsets.clear()
                sim=Simulator(10,scenario)
                states=[]
                for frame in range(81 if scenario=='Join/leave' else 15):
                    t=frame*.1;chunk,truth=sim.batch(t)
                    runtime.submit(chunk,10+t);runtime._process();state=runtime.engine.estimate(10+t+.001);states.append(state)
                if scenario in ('Disconnected','Coplanar'):
                    self.assertFalse(any(s['positions'] for s in states),scenario)
                elif scenario in ('Rapid motion','Packet loss','Missing links','Join/leave'):
                    self.assertTrue(any(s['positions'] for s in states),scenario)
                    fitted,_=align(np.array([state['positions'][n] for n in state['ids']]),truth)
                    self.assertLess(np.sqrt(np.mean((fitted-truth)**2)),.15,scenario)
                else:self.assertTrue(any(s['state']!='measured-supported' for s in states))
                if scenario=='Join/leave':
                    self.assertTrue(any(len(s['ids'])==7 for s in states));self.assertEqual(len(states[-1]['ids']),10)
        finally:await runtime.shutdown()
    async def test_simulation_log_replay_and_bounded_queue(self):
        runtime=Runtime(asyncio.get_running_loop(),Config(solver_budget_ms=500))
        try:
            with tempfile.TemporaryDirectory() as folder:
                path=Path(folder)/'session.jsonl'
                await runtime.start('simulation',count=6,scenario='Static')
                await runtime.recording(path);await asyncio.sleep(.65)
                state=runtime.snapshot();self.assertEqual(state['source'],'simulation');self.assertTrue(state['positions'],runtime.error)
                await runtime.stop();self.assertTrue(path.exists())
                csv=Path(folder)/'out.csv';export_csv(path,csv);self.assertIn('simulation',csv.read_text())
                self.assertIn('effective_gateway_us',(Path(folder)/'out-ranges.csv').read_text())
                await runtime.start('replay',path=path,speed=2);await asyncio.sleep(.6)
                self.assertEqual(runtime.snapshot()['source'],'replay')
                await runtime.stop()
                for _ in range(200):runtime.submit(b'junk')
                self.assertEqual(len(runtime.pending),128);self.assertEqual(runtime.dropped,72)
        finally:await runtime.shutdown()


if __name__=='__main__':unittest.main()
