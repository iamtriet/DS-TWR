"""Anchor-free swarm: confirmed broadcast DS-TWR and weighted SMACOF.

Coordinates have a relative gauge. Stable inter-node distances do not prove
absolute rest: a rigidly translating/rotating swarm remains unobservable.
"""
from collections import defaultdict,deque
from dataclasses import dataclass
import math,time,json
import numpy as np
from scipy.sparse.csgraph import shortest_path
from uwb3d_core import Engine,Range,mds
from uwb3d_protocol import MASK40,DTU,C

@dataclass
class Beacon:
    node:int;count:int;master:int;seq:int;network:int;boot:int;term:int
    tx:int;previous:int;actual:int;confirmed_previous:bool;peers:dict
    received:float;confirmed:bool=False;motion:dict|None=None
    @classmethod
    def unpack(cls,p,received):
        if p[:2]==b'\x41\x88':
            if len(p)<49 or int.from_bytes(p[5:7],'little')!=65535 or int.from_bytes(p[7:9],'little')!=p[12]:raise ValueError('Swarm MAC')
            p=p[9:]
        if len(p)<40 or p[:3]!=b'SC\x01':raise ValueError('Swarm header')
        get=lambda start,n:int.from_bytes(p[start:start+n],'little')
        node,count,master=p[3:6]
        if not 1<=node<=count<=30 or not 1<=master<=count or len(p)!=40+7*(count-1):raise ValueError('Swarm bounds')
        valid=get(32,4)
        if valid>>count or valid&(1<<(node-1)) or p[36]&~15 or (p[36]&8 and not p[36]&2):raise ValueError('Swarm flags')
        peers={};offset=40
        for peer in range(1,count+1):
            if peer==node:continue
            reply=get(offset,4);sequence=get(offset+4,2);gap=p[offset+6];offset+=7
            if valid&(1<<(peer-1)):
                if not reply:raise ValueError('Zero reply time')
                peers[peer]=(reply,sequence,gap)
        result=cls(node,count,master,get(6,2),get(8,4),get(12,4),get(16,4),get(20,5),get(25,2),get(27,5),bool(p[36]&1),peers,received)
        if p[36]&2:
            result.motion=dict(sensor='BNO085' if p[36]&8 else 'LIS2DH12',moving=bool(p[36]&4),score_mg=p[37],
                               quiet_ms=p[38]*20,sample_age_ms=p[39]*10,received=received)
        return result

class SwarmLedger:
    def __init__(self):
        self.frames=defaultdict(dict);self.boots={};self.done={};self.generation=0
        self.rejected=0;self.network=None;self.term=None
    def add(self,b):
        epoch=(b.network,b.master,b.term)
        if epoch!=self.term:
            self.frames.clear();self.done.clear();self.boots.clear();self.term=epoch
        if b.node in self.boots and self.boots[b.node]!=b.boot:
            # Every exchange involving a rebooted node must be rebuilt.
            self.frames.clear();self.done.clear()
        self.boots[b.node]=b.boot
        records=self.frames[b.node]
        if b.seq in records:return []
        records[b.seq]=b
        if b.confirmed_previous and b.previous in records:
            previous=records[b.previous]
            if previous.tx==b.actual and previous.boot==b.boot:previous.confirmed=True
            else:self.rejected+=1
        for node,frames in self.frames.items():
            for seq,frame in list(frames.items()):
                if b.received-frame.received>.4:del frames[seq]
        ranges=[]
        # For A0 -> B0 -> A1 -> B1, each RX reference explicitly names
        # its sender sequence. A0/B0/A1 require later TX confirmation.
        for bid,frames in self.frames.items():
            for b1 in list(frames.values())[-3:]:
                # B1 only reports an earlier RX: its scheduled TX cancels in
                # (TX_B1 - TX_B0) - (TX_B1 - RX_B1[A]). A0/B0/A1 still
                # require actual TX confirmation; no fourth-cycle wait needed.
                affected=b1.peers.items() if bid==b.node else ([(b.node,b1.peers[b.node])] if b.node in b1.peers else [])
                for aid,(db,aseq,qb) in affected:
                    a1=self.frames.get(aid,{}).get(aseq)
                    if a1 is None or not a1.confirmed or bid not in a1.peers:continue
                    da,bseq,qa=a1.peers[bid];b0=frames.get(bseq)
                    if b0 is None or not b0.confirmed or aid not in b0.peers:continue
                    d0,a0seq,q0=b0.peers[aid];a0=self.frames[aid].get(a0seq)
                    if a0 is None or not a0.confirmed:continue
                    key=tuple(sorted((aid,bid)));signature=(aid,a1.boot,a1.seq,b1.boot,b1.seq)
                    # Both directions can reconstruct overlapping exchanges;
                    # emit only increasing effective receipt epochs per link.
                    effective=(a0.received+a1.received)/2
                    if effective<=self.done.get(key,(-1,None))[0]:continue
                    pa=(a1.tx-a0.tx)&MASK40;pb=(b1.tx-b0.tx)&MASK40
                    if not 0<pa<2**32 or not 0<pb<2**32:continue
                    ra=pa-da;rb=pb-db
                    if min(ra,rb,d0,da)<=0:continue
                    tof=(ra*rb-d0*da)/(ra+rb+d0+da)
                    distance=tof*DTU*C
                    if not math.isfinite(distance) or not .01<distance<100: self.rejected+=1;continue
                    self.done[key]=(effective,signature);self.generation+=1
                    gaps=[q for q in (qb,qa,q0) if q!=255]
                    weight=math.exp(-max(0,max(gaps)-6)/3) if len(gaps)==3 else .25
                    ranges.append(Range(*key,distance,effective,b.received,self.generation,quality=max(.01,weight),nlos=bool(gaps and max(gaps)>6)))
        return ranges

def weighted_kabsch(points,reference,weights):
    w=np.asarray(weights,float);w=w/w.sum()
    ca=w@points;cb=w@reference
    u,_,vt=np.linalg.svd((points-ca).T@(w[:,None]*(reference-cb)))
    correction=np.eye(2);correction[-1,-1]=np.linalg.det(u@vt)
    rotation=u@correction@vt
    return (points-ca)@rotation+cb

class SwarmEngine(Engine):
    @staticmethod
    def _new_tracker():
        from swarm_localization import SwarmEKFTracker
        return SwarmEKFTracker()
    def __init__(self,config):
        super().__init__(config);self.stable_since={};self.old_ranges={};self.ekf=self._new_tracker();self.stationary=set();self.bias={};self.ekf_generation=-1
    def reset(self):
        super().reset();self.stable_since={};self.old_ranges={};self.ekf=self._new_tracker();self.stationary=set();self.ekf_generation=-1;self.swarm_ids=None
        self.motion_solver=None;self.cooperative_tracker=None
        self.published={}
    def ingest(self,r):
        # Residual body-mounted calibration is distinct from DW antenna OTP.
        # Missing calibration is zero correction and explicitly reported below.
        from dataclasses import replace
        bias=self.bias.get(tuple(sorted((r.a,r.b))),0.)
        return super().ingest(replace(r,distance=r.distance-bias))
    def _solve(self,ids,d,edges,now,t0):
        if self.config.reference_nodes:
            from swarm_motion import MotionReferenceSolver
            if getattr(self,'motion_solver',None) is None:self.motion_solver=MotionReferenceSolver(self.config.reference_nodes,direct_ranges=True)
            self.stationary=set(self.config.reference_nodes);motion_quality={}
            from swarm_range_window import range_window
            for node in ids:
                if node in self.config.reference_nodes:continue
                imu=self.registry.nodes[node].imu
                fresh=self.config.imu_motion_gating and imu and now-imu['received']<.25 and imu.get('sample_age_ms',1000)<=100
                if not fresh:continue
                # Acceleration alone cannot distinguish rest from constant
                # velocity. Require two quiet range trends as well.
                quiet_links=0
                for anchor in self.config.reference_nodes:
                    rows=self.matrix.history.get(tuple(sorted((node,anchor))),())
                    if not rows:continue
                    v=range_window(rows,rows[-1][0],.6)
                    if v and v['samples']>=12 and now-v['newest']<.25 and abs(v['radial_speed'])<.06 and v['sigma']<.04:
                        quiet_links+=1
                rest=not imu['moving'] and imu['quiet_ms']>=800 and quiet_links>=2
                motion_quality.setdefault(node,{}).update(
                    accelerometer_fresh=True,acceleration_quiet_ms=imu['quiet_ms'],quiet_range_links=quiet_links,relative_rest=rest)
                if rest:
                    self.stationary.add(node)
            result=self.motion_solver.solve(ids,edges,self.matrix.history,now,rest_nodes=self.stationary)
            for node,details in motion_quality.items():
                result[1].setdefault('node_quality',{}).setdefault(node,{}).update(details)
            result[1]['stationary']=sorted(self.stationary)
            return result
        from swarm_cooperative_tracker import CooperativeRangeTracker
        if getattr(self,'cooperative_tracker',None) is None:self.cooperative_tracker=CooperativeRangeTracker(self.config.swarm_origin)
        result=self.cooperative_tracker.solve(ids,d,edges,self.registry.nodes,self.matrix.history,now,self.config.imu_motion_gating)
        self.stationary=set(result[1]['stationary'])
        return result
    def estimate(self,now=None):
        now=time.monotonic() if now is None else now
        if not hasattr(self,'published'):self.published={}
        generation=self.last_generation;s=super().estimate(now)
        accepted=s['state'] in ('measured-supported','estimated')
        # An unchanged generation is a held observation, not a failed solve.
        if s['state']=='stale' and generation==self.last_generation and s['age'] is not None and s['age']<=self.config.max_age:
            s['state']='estimated';s['reason']='Holding last accepted estimate; waiting for new ranges'
            for node in s['ids']:
                if node in s['positions'] and node not in s['metrics']['unavailable_nodes']:s['node_states'][node]='estimated'
        direct=self.last_solution.get('range_domain',False)
        if accepted and not direct and self.ekf_generation!=self.last_generation:
            self.ekf_generation=self.last_generation
            for node,point in s['measured_positions'].items():
                sigma=self.last_solution.get('node_sigma',{}).get(node,max(.03,s['metrics'].get('rms') or .03))
                step=now-self.published.get(node,now-.04)
                s['positions'][node]=np.r_[self.ekf.update_one(node,np.array(point[:2]),now,sigma,node in self.stationary,max_step_dt=step),0.].tolist()
        # The base engine caches raw solver points. Never expose those on a
        # failed solve or a per-node outage after publishing filtered points.
        # Keep freshness flags and ages unchanged; this is a held coordinate.
        for node in s['positions']:
            self.published[node]=now
            if not direct and node in self.ekf.states:
                s['positions'][node]=np.r_[self.ekf.states[node][0][:2],0.].tolist()
                sigma=float(np.sqrt(max(0.,np.linalg.eigvalsh(self.ekf.states[node][1][:2,:2])[-1])))
                s['metrics'].setdefault('node_quality',{}).setdefault(node,{})['filter_position_sigma_m']=sigma
                s['uncertainty']=max(s.get('uncertainty') or 0.,sigma)
            if not direct and s['node_states'].get(node) in ('measured-supported','estimated') and self.ekf.status.get(node)!='updated':
                s['node_states'][node]='estimated'
                if s['state']=='measured-supported':s['state']='estimated'
        s['metrics'].update(method='Stationary reference + robust trilateration / SMACOF + planar EKF',stationary_nodes=sorted(self.stationary),body_calibration=bool(self.bias),absolute_motion_observable=False,ekf_rejected=self.ekf.rejected,
                            filter_status=dict(self.ekf.status),ekf_limited=self.ekf.limited,ekf_reacquired=self.ekf.reacquired)
        if direct:
            s['metrics']['method']='Timestamp-aware robust range-domain IEKF / fixed references'
            s['metrics']['range_domain']=True
            if self.last_solution.get('cooperative'):
                s['metrics']['method']='Cooperative range-domain IEKF + BNO085 motion / all nodes mobile'
                s['metrics']['cooperative']=True
                s['metrics']['range_updates']=self.last_solution.get('range_updates',0)
                s['metrics']['frame_nodes']=self.last_solution.get('frame_nodes',[])
                s['metrics']['frame_mode']=self.last_solution.get('frame_mode','relative-fallback')
                s['metrics']['origin_node']=self.config.swarm_origin
                if self.config.swarm_origin:s['metrics']['method']='UWB range-domain IEKF + BNO085 motion / stationary collector origin'
                if s['state'] in ('measured-supported','estimated'):
                    frame_nodes=s['metrics']['frame_nodes']
                    s['reason']=('All nodes mobile; display stabilized by quiet subgroup '+','.join(f'{n:04X}' for n in frame_nodes)) if frame_nodes else 'All nodes mobile; relative frame (no verified quiet subgroup); absolute motion unobserved'
                    if self.config.swarm_origin:s['reason']=f'Origin: stationary collector {self.config.swarm_origin:04X} (0,0); UWB-measured ranges; heading relative'
        degraded=self.last_solution.get('reference_degraded',False)
        s['metrics']['reference_degraded']=degraded
        s['metrics']['reference_warning']=self.last_solution.get('reference_warning','')
        if degraded and s['state'] in ('measured-supported','estimated'):
            s['state']='estimated';s['reason']=s['metrics']['reference_warning']
            for node in s['node_states']:
                if s['node_states'][node]=='measured-supported':s['node_states'][node]='estimated'
        # Fresh packets do not imply a geometrically trustworthy position.
        # Keep the live view, but expose inconsistent ranges instead of calling
        # every four-iteration IEKF output a supported measurement.
        fit=s['metrics'].get('rms')
        s['metrics']['geometry_inconsistent']=bool(direct and fit is not None and fit>self.config.max_rms_m)
        if s['metrics']['geometry_inconsistent'] and s['state'] in ('measured-supported','estimated'):
            s['state']='estimated'
            s['reason']+=f'; inconsistent UWB geometry (range-fit RMS {fit:.2f} m)'
            for node in s['node_states']:
                if node!=self.config.swarm_origin and s['node_states'][node]=='measured-supported':s['node_states'][node]='estimated'
        return s
