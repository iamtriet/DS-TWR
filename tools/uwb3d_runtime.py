"""Bounded U3D acquisition/estimation using Control Center's existing loop."""
import asyncio
import csv
import json
import math
import queue
import struct
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
import numpy as np
from uwb3d_protocol import (Decoder,encode,DISCOVERY,DISCOVERY_BODY,RANGE,range_payload,
    decode_range,HEARTBEAT,STATUS,NETWORK,IMU,TIMESTAMPS,CONFIG_RESPONSE,Broadcast,BroadcastLedger)
from uwb3d_core import Config,Engine,Matrix,Range,sparse_edges,align

SCENARIOS=('Static','Random motion','Translation','Rotation','Expansion','Separating groups',
           'Packet loss','NLOS bias','Missing links','Disconnected','Join/leave','Rapid motion','Coplanar')


class Recorder:
    def __init__(self,path,metadata):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.q=queue.Queue(2048);self.dropped=0;self.error=None;self.closed=False
        self.worker=threading.Thread(target=self._write,daemon=True);self.worker.start()
        self.put(dict(type='metadata',session_id=str(uuid.uuid4()),created_unix=time.time(),config=metadata))
    def put(self,row):
        if self.closed:return
        try:self.q.put_nowait(row)
        except queue.Full:self.dropped+=1
    def _write(self):
        try:
            with self.path.open('w',encoding='utf-8') as out:
                while True:
                    row=self.q.get()
                    if row is None:break
                    out.write(json.dumps(row,allow_nan=False)+'\n')
                out.write(json.dumps(dict(type='log_end',dropped=self.dropped))+'\n')
        except Exception as exc:self.error=str(exc)
    def close(self):
        self.closed=True
        if self.worker.is_alive():
            try:self.q.put(None,timeout=1)
            except queue.Full:raise RuntimeError('Log writer did not drain; log may be incomplete')
            self.worker.join(timeout=5)
            if self.worker.is_alive():raise RuntimeError('Log writer close timed out')
        if self.error:raise RuntimeError('Log write failed: '+self.error)


class Simulator:
    def __init__(self,count=30,scenario='Random motion',seed=42,noise=.02,sparse=False):
        if not 2<=count<=30 or scenario not in SCENARIOS:raise ValueError('Simulator settings')
        self.rng=np.random.default_rng(seed);self.count=count;self.scenario=scenario;self.noise=noise;self.sparse=sparse
        self.base=self.rng.uniform(-3,3,(count,3));self.phase=self.rng.uniform(0,6.28,(count,3));self.seq=0;self.frame=0
    def positions(self,t):
        p=self.base.copy();s=self.scenario
        if s=='Coplanar':p[:,2]=0
        elif s=='Translation':p+=np.array([t*.5,0,0])
        elif s=='Rotation':
            a=t*.3;r=np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1]])
            p=p@r.T
        elif s=='Expansion':p*=1+.15*math.sin(t)
        elif s in ('Separating groups','Disconnected'):p[self.count//2:,0]+=max(0,3*math.sin(t*.3))
        elif s not in ('Static','Join/leave'):p+=.2*np.sin((t*(4 if s=='Rapid motion' else .4))+self.phase)
        return p
    def packet(self,kind,stamp,payload):
        result=encode(kind,self.seq,stamp,payload);self.seq=(self.seq+1)&0xffffffff;return result
    def batch(self,t):
        p=self.positions(t);stamp=max(0,round(t*1e6));parts=[]
        active=list(range(1,self.count+1))
        if self.scenario=='Join/leave' and 3<t%10<7:active=active[:max(2,self.count-3)]
        if self.frame%10==0:
            for n in active:parts.append(self.packet(DISCOVERY,stamp,DISCOVERY_BODY.pack(n,n,0,1,0,0)))
        pairs=sparse_edges(active) if self.sparse else [(a,b) for a in active for b in active if a<b]
        for a,b in pairs:
            if self.scenario=='Disconnected' and (a<=self.count//2)!=(b<=self.count//2):continue
            if self.scenario=='Separating groups' and math.sin(t*.3)>.5 and (a<=self.count//2)!=(b<=self.count//2):continue
            loss=.25 if self.scenario=='Packet loss' else .5 if self.scenario=='Missing links' else 0.
            if self.rng.random()<loss:self.seq=(self.seq+1)&0xffffffff;continue
            distance=float(np.linalg.norm(p[a-1]-p[b-1])+self.rng.normal(0,self.noise))
            suspect=self.scenario=='NLOS bias' and (a+b)%7==0
            if suspect:distance+=.6
            parts.append(self.packet(RANGE,stamp,range_payload(a,b,max(.001,distance),self.frame,stamp,
                         quality=None,nlos=None))) # Hidden NLOS truth never becomes invented diagnostics.
        self.frame=(self.frame+1)&0xffffffff
        return b''.join(parts),p


class Runtime:
    def __init__(self,loop,config=None):
        self.loop=loop;self.config=(config or Config()).validate();self.engine=Engine(self.config)
        self.decoder=Decoder();self.ledger=BroadcastLedger();self.pending=deque();self.pending_lock=threading.Lock()
        self.state_lock=threading.Lock();self.latest={};self.tasks=[];self.executor=ThreadPoolExecutor(1,thread_name_prefix='uwb3d-solver')
        self.source='stopped';self.dropped=0;self.invalid=0;self.error='';self.offsets=deque(maxlen=100)
        self.ranges=deque(maxlen=20000);self.log=None;self.truth=None;self.truth_time=None;self.serial=None
        self.replay_paused=False;self.replay_speed=1.;self.replay_seek=0.;self.started=0.;self.generation=0;self.network={}
        self.gateway=None;self.outgoing=deque(maxlen=64);self.assignments={};self.last_selection=0.
        self.uart_ready=None;self.reset_requested=False
        self.probe_index=0
    def snapshot(self):
        with self.state_lock:return self.latest
    def submit(self,chunk,received=None):
        received=time.monotonic() if received is None else received
        with self.pending_lock:
            if len(self.pending)>=128:self.pending.popleft();self.dropped+=1
            self.pending.append((bytes(chunk),received))
    async def start(self,source='simulation',**options):
        await self.stop()
        self.engine=Engine(self.config);self.decoder=Decoder();self.ledger=BroadcastLedger();self.offsets.clear();self.pending.clear()
        self.truth=None;self.truth_time=None;self.ranges.clear();self.error='';self.invalid=0;self.dropped=0;self.network={}
        self.source=source;self.started=time.monotonic();self.generation+=1
        self.outgoing.clear();self.assignments.clear();self.uart_ready=asyncio.Event()
        self.last_selection=0.;self.probe_index=0
        with self.state_lock:self.latest={}
        if source=='simulation':producer=self._simulate(**options)
        elif source=='replay':producer=self._replay(**options)
        elif source=='uart':producer=self._uart(**options)
        elif source=='gateway':producer=self._gateway(**options)
        elif source=='reference':producer=self._gateway(reference=True,**options)
        elif source=='legacy':producer=self._idle()
        else:raise ValueError('Unsupported source')
        self.tasks=[asyncio.create_task(producer),asyncio.create_task(self._estimate())]
    async def stop(self):
        for task in self.tasks:task.cancel()
        if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tasks=[]
        # Wait for the single solver before replacing/resetting its state.
        await self.loop.run_in_executor(self.executor,lambda:None)
        with self.pending_lock:self.pending.clear()
        if self.serial:
            await asyncio.to_thread(self.serial.close);self.serial=None
        if self.gateway:
            errors=await self.gateway.stop()
            if errors:raise RuntimeError('Boards not stopped; retry Stop: '+'; '.join(errors))
            self.gateway=None
        if self.log:await asyncio.to_thread(self.log.close);self.log=None
        self.source='stopped'
    async def shutdown(self):
        await self.stop();self.executor.shutdown(wait=False,cancel_futures=True)
    async def _idle(self):
        while True:await asyncio.sleep(1)
    async def _simulate(self,count=30,scenario='Random motion',sparse=False):
        sim=Simulator(count,scenario,sparse=sparse);next_time=time.monotonic()
        while True:
            now=time.monotonic();chunk,truth=sim.batch(now-self.started)
            self.truth=truth;self.truth_time=now;self.submit(chunk,now)
            next_time+=.1
            await asyncio.sleep(max(.001,next_time-time.monotonic()))
    async def _uart(self,port,baud=115200):
        import serial
        while True:
            try:
                # CH340/Windows can keep a large timed read pending while bytes
                # arrive continuously. Drain available bytes without waiting for
                # a full 4096-byte block (~0.6 s in the six-board experiment).
                self.serial=await asyncio.to_thread(serial.Serial,port,baud,timeout=0)
                self.uart_ready.set();self.reset_requested=True
                self.error=''
                while True:
                    data=await asyncio.to_thread(self.serial.read,4096)
                    if data:self.submit(data)
                    while self.outgoing:
                        command=self.outgoing.popleft()
                        await asyncio.to_thread(self.serial.write,(command+'\n').encode('ascii'))
                    await asyncio.sleep(.005)
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.uart_ready.clear()
                self.error=f'UART disconnected: {exc}; retrying'
                if self.serial:await asyncio.to_thread(self.serial.close);self.serial=None
                await asyncio.sleep(2)
    async def _gateway(self,boards,port,reference=False):
        from uwb3d_gateway import GatewayFleet
        def emit(kind,value):
            if kind=='gateway':self.error=value
            if self.log:self.log.put(dict(type='gateway_event',elapsed=time.monotonic()-self.started,kind=kind,value=value))
        if reference:
            from reference_gateway import ReferenceFleet
            self.gateway=ReferenceFleet(boards,emit,self.config.reference_period_ms,self.config.reference_tag,self.config.reference_anchor_mask)
        else:self.gateway=GatewayFleet(boards,emit,self.config.gateway_slot_ms,self.config.gateway_superframe_ms,self.config.gateway_network_id)
        try:
            # Start UART first so boot/identity packets cannot be missed.
            reader=asyncio.create_task(self._uart(port))
            try:
                await asyncio.wait_for(self.uart_ready.wait(),10)
                await self.gateway.configure();self.error=('Reference TDMA DS-TWR active — STS/AES, RF/security unqualified' if reference else 'Experimental gateway active — radio throughput unqualified')
                await reader
            finally:
                reader.cancel();await asyncio.gather(reader,return_exceptions=True)
        except asyncio.CancelledError:raise
        except Exception as exc:
            self.error=str(exc)
            errors=await self.gateway.stop()
            if errors:self.error+='; stop unresolved: '+'; '.join(errors)
    async def _replay(self,path,speed=1.,seek=0.):
        if speed<=0 or seek<0:raise ValueError('Replay speed/seek')
        self.replay_speed=speed;self.replay_paused=False
        previous=None
        try:
            with Path(path).open(encoding='utf-8') as stream:
                while True:
                    line=await asyncio.to_thread(stream.readline,1000001)
                    if not line:break
                    if len(line)>1000000:raise ValueError('Replay record too large')
                    record=json.loads(line)
                    if record.get('type')!='raw' or record['elapsed']<seek:continue
                    elapsed=record['elapsed']
                    if previous is not None:
                        remaining=max(0.,elapsed-previous)/self.replay_speed
                        while remaining>0:
                            if self.replay_paused:await asyncio.sleep(.05);continue
                            wait=min(.05,remaining);await asyncio.sleep(wait);remaining-=wait
                    while self.replay_paused:await asyncio.sleep(.05)
                    self.submit(bytes.fromhex(record['hex']));previous=elapsed
                    await asyncio.sleep(0)
            self.error='Replay complete; last state will become stale'
        except asyncio.CancelledError:raise
        except Exception as exc:self.error=f'Replay error: {exc}'
    def _process(self):
        if self.reset_requested:
            self.engine.matrix=Matrix(self.config);self.engine.reset();self.decoder=Decoder();self.offsets.clear();self.reset_requested=False
        with self.pending_lock:chunks=list(self.pending);self.pending.clear()
        for chunk,received in chunks:
            if self.log:self.log.put(dict(type='raw',elapsed=received-self.started,hex=chunk.hex()))
            for packet in self.decoder.feed(chunk):
                try:
                    if packet.reset:
                        self.engine.matrix=Matrix(self.config);self.engine.reset();self.offsets.clear()
                    sent=packet.timestamp_us/1e6
                    if self.source=='replay':sent/=self.replay_speed
                    self.offsets.append(received-sent);offset=min(self.offsets)
                    if packet.kind==DISCOVERY:
                        if len(packet.payload)!=DISCOVERY_BODY.size:raise ValueError('discovery size')
                        id,uid,major,minor,patch,caps=DISCOVERY_BODY.unpack(packet.payload)
                        self.engine.registry.register(id,str(uid),received,firmware=f'{major}.{minor}.{patch}',capabilities=caps)
                    elif packet.kind==RANGE:
                        r=decode_range(packet.payload);effective=r.pop('effective_us')/1e6
                        if self.source=='replay':effective/=self.replay_speed
                        if self.engine.ingest(Range(**r,effective=effective+offset,received=received)):
                            self.ranges.append(received)
                    elif packet.kind==TIMESTAMPS:
                        for a,b,seq,d in self.ledger.add(Broadcast.unpack(packet.payload)):
                            # Timestamp exchange spans a superframe; no common
                            # gateway measurement time is supplied by this report.
                            self.network['timestamp_ranges_unfused']=self.network.get('timestamp_ranges_unfused',0)+1
                    elif packet.kind==HEARTBEAT:
                        if len(packet.payload)!=2:raise ValueError('heartbeat length')
                        id=struct.unpack('<H',packet.payload)[0]
                        self.engine.registry.nodes[id].last=received
                    elif packet.kind==STATUS:
                        if len(packet.payload)!=5:raise ValueError('status length')
                        id,battery,flags=struct.unpack('<HHB',packet.payload)
                        if battery>10000 and battery!=65535:raise ValueError('Battery percentage bounds')
                        node=self.engine.registry.nodes[id];node.last=received;node.battery=None if battery==65535 else battery/100
                    elif packet.kind==IMU:
                        if len(packet.payload)!=15:raise ValueError('IMU length')
                        id,quality,acc,gyro,age=struct.unpack('<HBfff',packet.payload)
                        if not all(math.isfinite(v) and v>=0 for v in (acc,gyro,age)):raise ValueError('IMU values')
                        node=self.engine.registry.nodes[id];node.imu=dict(quality=quality,acc=acc,gyro=gyro,stamp=received-age)
                        if self.config.imu_motion_gating and quality>=2 and age<.2 and (acc>.5 or gyro>.15):
                            self.engine.filter.reset() # Norm telemetry cannot provide vector inertial propagation.
                    elif packet.kind==NETWORK:
                        if len(packet.payload)!=10:raise ValueError('network length')
                        sf,sync,mode,period=struct.unpack('<IBBI',packet.payload)
                        previous=self.network.get('superframe')
                        if previous is not None and ((sf-previous)&0xffffffff)>=0x80000000:
                            self.engine.matrix=Matrix(self.config);self.engine.reset()
                        self.network=dict(superframe=sf,synchronized=bool(sync),mode=mode,period_us=period)
                    elif packet.kind==CONFIG_RESPONSE:
                        if len(packet.payload)!=6:raise ValueError('assignment acknowledgement length')
                        id,mask=struct.unpack('<HI',packet.payload)
                        self.assignments[id]=mask
                except (ValueError,KeyError,struct.error) as exc:self.invalid+=1;self.error=str(exc)
    def _step(self):
        self._process();now=time.monotonic();state=self.engine.estimate(now)
        state['source']=self.source;state['error']=self.error;state['network']=dict(self.network)
        if self.source=='gateway' and state['ids']==list(range(1,7)) and self.network.get('period_us',500000)<=150000:
            # Six-node startup survey: rotate the one omitted edge of node 6.
            # Otherwise a raised node outside the first tetrahedron can remain
            # unobservable forever, preventing geometry-driven selection.
            state['network']['neighbor_policy']='six-node rotating sparse survey'
            if now-self.last_selection>.5:
                self.last_selection=now;mask=31^(1<<self.probe_index);self.probe_index=(self.probe_index+1)%5
                self.outgoing.append(f'net assign 6 {mask:X}')
        elif self.source=='gateway' and state['state']=='measured-supported' and now-self.last_selection>5:
            self.last_selection=now;ids=state['ids']
            quality={tuple(sorted((r['a'],r['b']))):
                (r['quality'] if r['quality'] is not None else .5)*math.exp(-max(0,now-r['effective'])/self.config.max_age)*(.2 if r['nlos'] else 1)
                for r in state['edges']}
            pairs=sparse_edges(ids,state['positions'],quality)
            for id in ids:
                if id==1:continue
                peers=[a for a,b in pairs if b==id];mask=sum(1<<(p-1) for p in peers)
                if 1<=id<=30 and len(peers)==min(4,id-1) and self.assignments.get(id)!=mask:
                    self.outgoing.append(f'net assign {id} {mask:X}')
        state['metrics'].update(measurement_hz=sum(now-t<1 for t in self.ranges),parser=dict(self.decoder.stats),
            queue_drops=self.dropped,invalid=self.invalid,log_drops=self.log.dropped if self.log else 0,
            log_error=self.log.error if self.log else None)
        if self.truth is not None and self.truth_time is not None and now-self.truth_time<.3 and state['positions']:
            ids=state['ids'];estimated=np.array([state['positions'][n] for n in ids]);truth=self.truth[np.array(ids)-1]
            fitted,_=align(estimated,truth);state['simulation_rmse']=float(np.sqrt(np.mean(np.sum((fitted-truth)**2,axis=1))))
        state['published_at']=time.monotonic()
        state['metrics']['measurement_to_publish_ms']=1000*(state['published_at']-min(r['effective'] for r in state['edges'])) if state['edges'] else None
        if self.log:self.log.put(dict(type='state',elapsed=now-self.started,state=state))
        return state
    async def _estimate(self):
        while True:
            start=time.monotonic()
            try:
                state=await self.loop.run_in_executor(self.executor,self._step)
                with self.state_lock:self.latest=state
            except asyncio.CancelledError:raise
            except Exception as exc:
                self.error=f'Estimator error: {exc}'
                with self.state_lock:
                    if self.latest:self.latest=dict(self.latest,state='stale',error=self.error)
            await asyncio.sleep(max(.001,1/self.config.target_rate_hz-(time.monotonic()-start)))
    async def reset(self):
        await self.loop.run_in_executor(self.executor,self.engine.reset)
    async def recording(self,path=None):
        if self.log:await asyncio.to_thread(self.log.close);self.log=None
        if path:self.log=Recorder(path,dict(config=asdict(self.config),source=self.source))
    def legacy(self,a,b,distance,stamp):
        if self.source!='legacy':return
        parts=[]
        for id in (a,b):parts.append(encode(DISCOVERY,self.generation,round(stamp*1e6),DISCOVERY_BODY.pack(id,id,1,0,13,0)));self.generation+=1
        parts.append(encode(RANGE,self.generation,round(stamp*1e6),range_payload(a,b,distance or 0,self.generation,round(stamp*1e6),valid=distance is not None)));self.generation+=1
        self.submit(b''.join(parts),stamp)


def export_csv(source,destination):
    ranges_path=Path(destination).with_name(Path(destination).stem+'-ranges.csv')
    decoder=Decoder()
    with Path(source).open(encoding='utf-8') as inp,Path(destination).open('w',newline='',encoding='utf-8') as out,ranges_path.open('w',newline='',encoding='utf-8') as raw_out:
        writer=csv.writer(out);writer.writerow(('elapsed','source','id','x','y','z','tracking_state','rms_m','solver_ms'))
        ranges=csv.writer(raw_out);ranges.writerow(('receipt_elapsed','gateway_us','source_id','destination_id','distance_m','measurement_sequence','effective_gateway_us','quality','nlos','valid'))
        for line in inp:
            row=json.loads(line)
            if row.get('type')=='raw':
                for packet in decoder.feed(bytes.fromhex(row['hex'])):
                    if packet.kind!=RANGE:continue
                    try:r=decode_range(packet.payload)
                    except ValueError:continue
                    ranges.writerow((row['elapsed'],packet.timestamp_us,r['a'],r['b'],r['distance'],r['sequence'],r['effective_us'],r['quality'],r['nlos'],r['valid']))
            if row.get('type')!='state':continue
            s=row['state']
            for id,p in s['positions'].items():writer.writerow((row['elapsed'],s['source'],id,*p,s['state'],s['metrics']['rms'],s['metrics']['solver_ms']))
