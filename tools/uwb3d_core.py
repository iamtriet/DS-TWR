"""Transport-independent relative 3D estimation. No fixed physical anchors."""
import itertools
import math
import time
from dataclasses import dataclass,asdict,replace
from collections import deque
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse.csgraph import shortest_path
from threadpoolctl import threadpool_limits
from uwb3d_protocol import node_id


@dataclass
class Config:
    algorithm:str='legacy'
    swarm_calibration:str=''
    dimensions:int=3
    max_nodes:int=30
    target_rate_hz:float=10.
    max_age:float=.5
    node_timeout:float=3.
    prediction_limit:float=.3
    solver_budget_ms:float=75.
    max_evaluations:int=30
    noise_m:float=.03
    robust_scale_m:float=.08
    max_rms_m:float=.15
    max_uncertainty_m:float=.5
    temporal_weight:float=.1
    filtering:bool=True
    range_filtering:bool=False
    motion_profile:bool=False
    moving_node:int=0
    reference_nodes:tuple=()
    swarm_origin:int=0
    imu_motion_gating:bool=True
    gateway_slot_ms:int=10
    gateway_superframe_ms:int=500
    gateway_network_id:int=0x43594231
    reference_period_ms:int=200
    reference_tag:int=0
    reference_anchor_mask:int=0
    def validate(self):
        if not 0<=self.swarm_origin<=17:raise ValueError('Swarm origin ID')
        if not 0<=self.moving_node<=65534:raise ValueError('Moving node ID')
        if self.reference_nodes and (len(self.reference_nodes)<3 or len(set(self.reference_nodes))!=len(self.reference_nodes) or any(not 1<=n<=65534 for n in self.reference_nodes)):raise ValueError('Reference node IDs')
        if self.dimensions not in (2,3):raise ValueError('Choose planar 2D or relative 3D')
        if not 150<=self.reference_period_ms<=5000 or not 0<=self.reference_tag<=9 or not 0<=self.reference_anchor_mask<512:raise ValueError('Reference schedule/roles')
        if not 2<=self.max_nodes<=128:raise ValueError('Capacity must be 2–128')
        if not 1<=self.target_rate_hz<=30:raise ValueError('Publication rate must be 1–30 Hz')
        for k in ('max_age','node_timeout','prediction_limit','solver_budget_ms','noise_m','robust_scale_m','max_rms_m','max_uncertainty_m'):
            if not math.isfinite(getattr(self,k)) or getattr(self,k)<=0:raise ValueError(k+' must be positive')
        if not 1<=self.max_evaluations<=200 or not 0<=self.temporal_weight<=10:raise ValueError('Solver settings')
        if not 10<=self.gateway_slot_ms<=50 or not 40<=self.gateway_superframe_ms<=500 or not 0<=self.gateway_network_id<=0xffffffff:raise ValueError('Gateway timing/network settings')
        return self


@dataclass
class Node:
    id:int
    uid:str
    name:str
    last:float
    firmware:str='unknown'
    battery:float|None=None
    capabilities:int=0
    imu:dict|None=None


class Registry:
    def __init__(self,capacity):self.capacity=capacity;self.nodes={};self.events=deque(maxlen=256);self.active=set()
    def register(self,id,uid,now,name=None,firmware='unknown',capabilities=0):
        node_id(id);uid=str(uid)
        if id in self.nodes and self.nodes[id].uid!=uid:raise ValueError('Duplicate node ID with different UID')
        if any(n.uid==uid and n.id!=id for n in self.nodes.values()):raise ValueError('UID already registered with another ID')
        if id not in self.nodes:
            if len(self.nodes)>=self.capacity:raise ValueError('Node capacity exceeded')
            self.nodes[id]=Node(id,uid,name or f'N{id:04X}',now,firmware,capabilities=capabilities)
            self.events.append(('join',id,now))
        else:
            self.nodes[id].last=now
            if firmware!='unknown':self.nodes[id].firmware=firmware;self.nodes[id].capabilities=capabilities
            if name:self.nodes[id].name=name
        return self.nodes[id]
    def online(self,now,timeout):
        active={n.id for n in self.nodes.values() if now-n.last<=timeout}
        for id in self.active-active:self.events.append(('offline',id,now))
        for id in active-self.active:self.events.append(('online',id,now))
        self.active=active
        return sorted(active)
    def remove(self,id,now):
        if self.nodes.pop(id,None):self.events.append(('leave',id,now))


@dataclass
class Range:
    a:int
    b:int
    distance:float
    effective:float
    received:float
    sequence:int
    quality:float|None=None
    nlos:bool|None=None
    valid:bool=True
    raw_distance:float|None=None


class AdaptiveRangeFilter:
    """Causal per-link filtering, retaining raw measurements in Matrix.history.

    Fast tracking uses a three-sample median and noise-normalized persistent
    innovation to switch bandwidth. The legacy profile uses a Hampel guard
    and a One Euro style low pass. Neither assumes a ground-truth distance.
    """
    def __init__(self):
        self.samples=deque(maxlen=7);self.stamp=None;self.value=None
        self.raw=None;self.speed=0.;self.pending=None;self.rejected=0
        self.recent=deque(maxlen=3);self.differences=deque(maxlen=21)
        self.direction=0;self.motion_count=0;self.motion_until=0.
    def update(self,value,stamp,max_gap=2.,responsive=False):
        if self.stamp is None or stamp-self.stamp>max_gap:
            self.samples.clear();self.samples.append(value)
            self.stamp=stamp;self.raw=self.value=value;self.speed=0.;self.pending=None
            self.recent.clear();self.recent.append(value);self.differences.clear()
            self.direction=0;self.motion_count=0;self.motion_until=0.
            return value
        dt=stamp-self.stamp
        if dt<=0:return None
        if responsive:
            # A raw derivative amplifies stationary UWB noise and opens the old
            # speed-adaptive filter. Detect persistent innovation instead, using
            # first differences to estimate noise without treating a ramp as noise.
            self.differences.append(value-self.raw);self.recent.append(value)
            delta=np.array(self.differences)
            sigma=1.4826*float(np.median(np.abs(delta-np.median(delta))))/math.sqrt(2.)
            sample=float(np.median(self.recent));innovation=sample-self.value
            direction=1 if innovation>0 else -1
            if abs(innovation)>max(.025,2.5*sigma):
                self.motion_count=self.motion_count+1 if direction==self.direction else 1
                self.direction=direction
            else:self.motion_count=0;self.direction=0
            if self.motion_count>=3:self.motion_until=stamp+.35
            moving=stamp<self.motion_until
            cutoff=3. if moving else .25
            alpha=1./(1.+1./(2*math.pi*cutoff*dt))
            self.value+=alpha*(sample-self.value)
            self.speed=(sample-self.raw)/dt;self.raw=value;self.stamp=stamp
            self.samples.append(value)
            return self.value
        center=float(np.median(self.samples))
        scale=1.4826*float(np.median(np.abs(np.array(self.samples)-center)))
        threshold=max(.10,4*scale)
        if not responsive and len(self.samples)>=5 and abs(value-center)>threshold:
            consistent=self.pending is not None and (value-center)*(self.pending-center)>0 and abs(value-self.pending)<threshold
            if not consistent:
                self.pending=value;self.rejected+=1;return None
            self.samples.clear()  # A real step must not stay pinned to old history.
        self.pending=None;self.samples.append(value)
        alpha=lambda cutoff:1./(1.+1./(2*math.pi*cutoff*dt))
        self.speed+=alpha(1.)*((value-self.raw)/dt-self.speed)
        cutoff=(1.5+4.*abs(self.speed)) if responsive else .18+2.*abs(self.speed)
        self.value+=alpha(cutoff)*(value-self.value)
        self.raw=value;self.stamp=stamp
        return self.value


class Matrix:
    def __init__(self,config):self.config=config;self.links={};self.history={};self.attempts=0;self.success=0;self.generation=0;self.filters={};self.seen={};self.quarantine={};self.geometry={};self.geometry_stamp=0.
    def update(self,r):
        node_id(r.a);node_id(r.b)
        if r.a==r.b or not all(math.isfinite(x) for x in (r.effective,r.received)):raise ValueError('range identity/time')
        if r.effective>r.received+.01:raise ValueError('Measurement in future')
        key=tuple(sorted((r.a,r.b)));self.attempts+=1
        previous=self.seen.get(key)
        if previous:
            delta=(r.sequence-previous.sequence)&0xffffffff
            if delta==0 or delta>=0x80000000 or r.effective<previous.effective:return False
        if not r.valid:return False
        if not math.isfinite(r.distance) or not 0<r.distance<=1000:raise ValueError('range bounds')
        self.seen[key]=r
        self.history.setdefault(key,deque(maxlen=120)).append((r.effective,r.distance))
        if self.config.range_filtering:
            filtered=self.filters.setdefault(key,AdaptiveRangeFilter()).update(r.distance,r.effective,self.config.max_age,self.config.motion_profile)
            if filtered is None:return False
            r=replace(r,distance=filtered,raw_distance=r.distance)
        self.success+=1;self.generation+=1;self.links[key]=r
        return True
    def arrays(self,ids,now):
        n=len(ids);d=np.full((n,n),np.nan);age=np.full((n,n),np.inf);np.fill_diagonal(d,0.);np.fill_diagonal(age,0.)
        index={id:i for i,id in enumerate(ids)};edges=[]
        newest=max((r.effective for (a,b),r in self.links.items() if a in index and b in index),default=now)
        for (a,b),r in self.links.items():
            if a not in index or b not in index:continue
            i,j=index[a],index[b];age[i,j]=age[j,i]=max(0.,now-r.effective)
            if age[i,j]<=self.config.max_age and (not self.config.motion_profile or newest-r.effective<=.15):
                d[i,j]=d[j,i]=r.distance;edges.append(r)
        self.inconsistent=[]
        if self.config.range_filtering:
            # Reject long inconsistent edges, never invent replacement distances.
            # Two witnesses handle moderate violations; coherent motion frames
            # also reject one gross violation. Verify remaining rigidity below.
            for _ in range(2):
                candidates=[]
                for r in edges:
                    i,j=index[r.a],index[r.b]
                    excess=[d[i,j]-d[i,k]-d[k,j] for k in range(n)
                            if k not in (i,j) and np.isfinite(d[i,k]+d[k,j])]
                    excess.sort(reverse=True)
                    # Motion arrays already enforce a 150 ms observation span.
                    # A gross triangle violation must not deform the reference
                    # group just because the fast tracking profile is selected.
                    if len(excess)>=2 and excess[1]>.15:candidates.append((excess[1],r))
                    elif self.config.motion_profile and excess and excess[0]>.20:candidates.append((excess[0],r))
                if not candidates:break
                _,bad=max(candidates,key=lambda item:item[0]);edges.remove(bad)
                self.quarantine[tuple(sorted((bad.a,bad.b)))]=now+5.
                self.inconsistent.append((bad.a,bad.b));i,j=index[bad.a],index[bad.b];d[i,j]=d[j,i]=np.nan
            for key,until in list(self.quarantine.items()):
                if now>=until:
                    a,b=key;link=self.links.get(key)
                    supported=(a in self.geometry and b in self.geometry and
                               now-self.geometry_stamp<=self.config.max_age and link is not None and
                               abs(np.linalg.norm(self.geometry[a]-self.geometry[b])-link.distance)<.10)
                    if supported:
                        del self.quarantine[key];continue
                a,b=key
                if a in index and b in index:
                    edges=[r for r in edges if tuple(sorted((r.a,r.b)))!=key]
                    d[index[a],index[b]]=d[index[b],index[a]]=np.nan
                    if key not in self.inconsistent:self.inconsistent.append(key)
        return d,age,edges


def align(points,reference):
    """O(3), including reflection: ranges cannot distinguish chirality."""
    a=points-points.mean(0);b=reference-reference.mean(0)
    u,_,vt=np.linalg.svd(a.T@b);rot=u@vt
    return a@rot+reference.mean(0),bool(np.linalg.det(rot)<0)


def align_stationary_majority(points,reference,excluded=None):
    """Relative display frame using a majority presumed stationary, not a lock.

    Leave-one-out alignment prevents one moving board dragging the whole map.
    It does not establish a world frame when the majority moves together.
    """
    if len(points)<4:return align(points,reference)
    best=None
    for omitted in ([excluded] if excluded is not None else range(len(points))):
        keep=np.arange(len(points))!=omitted
        a=points[keep];b=reference[keep];u,_,vt=np.linalg.svd((a-a.mean(0)).T@(b-b.mean(0)));rot=u@vt
        fitted=(points-a.mean(0))@rot+b.mean(0)
        score=float(np.mean(np.sort(np.linalg.norm(fitted-reference,axis=1))[:-1]**2))
        if best is None or score<best[0]:best=(score,fitted,bool(np.linalg.det(rot)<0))
    return best[1],best[2]


def mds(distances,dimensions=3):
    if not np.all(np.isfinite(distances)):raise ValueError('MDS requires complete distances')
    n=len(distances);h=np.eye(n)-np.ones((n,n))/n
    vals,vec=np.linalg.eigh(-.5*h@(distances**2)@h)
    order=np.argsort(vals)[::-1][:dimensions]
    if len(order)<dimensions or vals[order[-1]]<max(vals[order[0]],1e-9)*1e-5:raise ValueError(f'Insufficient {dimensions}D geometry')
    points=np.zeros((n,3));points[:,:dimensions]=vec[:,order]*np.sqrt(np.maximum(vals[order],0))
    return points


def canonical(points,dimensions=3):
    p=points-points[0];distance=np.linalg.norm(p[1])
    if distance<1e-6:raise ValueError('Reference baseline too short')
    x=p[1]/distance
    if dimensions==2:
        return p@np.stack((x,np.array([-x[1],x[0],0.]),np.array([0.,0.,1.])),axis=1)
    z=np.cross(x,p[2]);zn=np.linalg.norm(z)
    if zn<1e-6:raise ValueError('Reference triplet is collinear')
    z/=zn;y=np.cross(z,x)
    return p@np.stack((x,y,z),axis=1)


def planar_lateration(distances):
    """Seed from a measured triangle and three known neighbors per new node."""
    n=len(distances)
    for clique in itertools.islice(itertools.combinations(range(n),3),256):
        sub=distances[np.ix_(clique,clique)]
        if not np.all(np.isfinite(sub)):continue
        try:triangle=mds(sub,2)
        except ValueError:continue
        points={i:p for i,p in zip(clique,triangle)}
        while len(points)<n:
            changed=False
            for i in range(n):
                if i in points:continue
                neighbors=[j for j in points if math.isfinite(distances[i,j])]
                if len(neighbors)<3:continue
                j0=neighbors[0];p0=points[j0][:2];d0=distances[i,j0]
                matrix=np.array([2*(points[j][:2]-p0) for j in neighbors[1:]])
                rhs=np.array([d0*d0-distances[i,j]**2+points[j][:2]@points[j][:2]-p0@p0 for j in neighbors[1:]])
                if np.linalg.matrix_rank(matrix)<2:continue
                xy=np.linalg.lstsq(matrix,rhs,rcond=None)[0];points[i]=np.r_[xy,0.];changed=True
            if not changed:break
        if len(points)==n:return np.array([points[i] for i in range(n)])
    raise ValueError('Fresh planar ranges lack a noncollinear triangle/three-neighbor scaffold')


def lateration(distances):
    """A noncoplanar 4-clique followed by four-known-neighbor lateration.

    Unlike shortest-path MDS, this preserves the side of each new node when
    its four predecessors supply globally identifying 3D constraints.
    """
    n=len(distances)
    for clique in itertools.islice(itertools.combinations(range(n),4),256):
        sub=distances[np.ix_(clique,clique)]
        if not np.all(np.isfinite(sub)):continue
        try:tetra=mds(sub)
        except ValueError:continue
        points={i:p for i,p in zip(clique,tetra)}
        while len(points)<n:
            changed=False
            for i in range(n):
                if i in points:continue
                neighbors=[j for j in points if math.isfinite(distances[i,j])]
                if len(neighbors)<4:continue
                j0=neighbors[0];p0=points[j0];d0=distances[i,j0]
                matrix=np.array([2*(points[j]-p0) for j in neighbors[1:]])
                rhs=np.array([d0*d0-distances[i,j]**2+points[j]@points[j]-p0@p0 for j in neighbors[1:]])
                if np.linalg.matrix_rank(matrix)<3:continue
                points[i]=np.linalg.lstsq(matrix,rhs,rcond=None)[0];changed=True
            if not changed:break
        if len(points)==n:return np.array([points[i] for i in range(n)])
    raise ValueError('Sparse graph lacks a verified noncoplanar lateration scaffold; global uniqueness unproven')


def sparse_edges(ids,positions=None,quality=None):
    """Four-predecessor lateration scaffold, selected by link score and geometry.

    This schedule is a candidate; the estimator independently checks its rank.
    """
    result=set();quality=quality or {}
    for i,node in enumerate(ids):
        earlier=ids[:i]
        if i<5:peers=earlier
        else:
            candidates=sorted(earlier,key=lambda p:quality.get(tuple(sorted((node,p))),.5),reverse=True)[:8]
            def score(peers):
                q=sum(quality.get(tuple(sorted((node,p))),.5) for p in peers)
                if positions and all(p in positions for p in peers):
                    pts=np.array([positions[p] for p in peers]);sv=np.linalg.svd(pts[1:]-pts[0],compute_uv=False)
                    q*=max(.001,float(sv[-1]/max(sv[0],1e-9)))
                return q
            peers=max(itertools.combinations(candidates,4),key=score)
        result.update(tuple(sorted((node,p))) for p in peers)
    return sorted(result)


def six_neighbor_mask(history,links,now,step,max_age,inconsistent=()):
    """Four scheduled peers plus deterministic exploration for six-node fleets.

    Scores are host heuristics, not radio CIR/NLOS measurements. Every third
    selection includes the lowest scoring peer, avoiding permanent starvation.
    """
    scores={};bad={tuple(sorted(pair)) for pair in inconsistent}
    for peer in range(1,6):
        key=(peer,6);r=links.get(key);samples=list(history.get(key,()))[-15:]
        noise=0.
        if len(samples)>=5:
            # Deviation from interpolation removes constant-speed motion and
            # accounts for irregular sampling before estimating jitter.
            errors=[]
            for (ta,a),(tb,b),(tc,c) in zip(samples,samples[1:],samples[2:]):
                if tc>ta:errors.append(abs(b-(a+(c-a)*(tb-ta)/(tc-ta))))
            if errors:noise=float(np.median(errors))
        freshness=math.exp(-max(0,now-r.effective)/max_age) if r else 0.
        scores[peer]=freshness/(1+(noise/.03)**2)*(.1 if key in bad else 1.)
    weakest=min(scores,key=lambda peer:(scores[peer],peer))
    if any(len(history.get((p,6),()))<5 for p in range(1,6)):
        return 31^(1<<(step%5)),scores  # Bootstrap has no reliable noise estimate.
    omitted=weakest
    if step%3==2:
        alternatives=[p for p in range(1,6) if p!=weakest]
        omitted=alternatives[(step//3)%len(alternatives)]
    return 31^(1<<(omitted-1)),scores


class CVFilter:
    def __init__(self):self.x=None;self.cov=None;self.stamp=None
    def reset(self):self.__init__()
    def update(self,positions,stamp,sigma):
        n=len(positions)
        if self.x is None or len(self.x)!=n or stamp<=self.stamp or stamp-self.stamp>1:
            self.x=np.column_stack((positions,np.zeros_like(positions)));self.cov=np.tile(np.eye(6)[None],(n,1,1))*.1
        else:
            dt=stamp-self.stamp;f=np.eye(6);f[:3,3:]=np.eye(3)*dt
            predicted=self.x@f.T
            g=np.vstack((np.eye(3)*dt*dt/2,np.eye(3)*dt))
            q=g@g.T*9. # 3 m/s² acceleration uncertainty, actual sample interval.
            for i in range(n):
                p=f@self.cov[i]@f.T+q
                innovation=positions[i]-predicted[i,:3];s=p[:3,:3]+np.eye(3)*sigma*sigma
                if innovation@np.linalg.solve(s,innovation)>16:
                    # A maneuver increases process uncertainty; don't freeze or
                    # instantly snap to a noisy position measurement.
                    p+=q*15;s=p[:3,:3]+np.eye(3)*sigma*sigma
                k=p[:,:3]@np.linalg.inv(s);predicted[i]+=k@innovation
                h=np.zeros((3,6));h[:,:3]=np.eye(3);a=np.eye(6)-k@h
                p=a@p@a.T+k@(np.eye(3)*sigma*sigma)@k.T
                self.cov[i]=p
            self.x=predicted
        self.stamp=stamp
        return self.x[:,:3].copy()
    def predict(self,now):
        dt=max(0.,now-self.stamp)
        return self.x[:,:3]+dt*self.x[:,3:]


class Engine:
    def __init__(self,config=None):
        self.config=(config or Config()).validate();self.registry=Registry(self.config.max_nodes);self.matrix=Matrix(self.config)
        self.previous=None;self.previous_ids=[];self.accepted_at=None;self.observed_at=None;self.filter=CVFilter()
        self.frame_reference=None;self.frame_ids=[]
        self.node_cache={}
        self.reacquire={}
        self.last_generation=-1;self.last_solution={};self.solve_times=deque(maxlen=100);self.last_solve=0.;self.accepts=deque(maxlen=100)
    def reset(self):
        self.frame_reference=None;self.frame_ids=[]
        self.matrix.geometry={};self.matrix.geometry_stamp=0.
        self.node_cache={}
        self.reacquire={}
        self.previous=None;self.filter.reset();self.accepted_at=None;self.observed_at=None;self.last_generation=-1;self.last_solution={}
    def ingest(self,r):
        if r.a not in self.registry.nodes or r.b not in self.registry.nodes:raise ValueError('Range from unregistered node')
        for n in (r.a,r.b):self.registry.nodes[n].last=r.received
        return self.matrix.update(r)
    def estimate(self,now=None):
        now=time.monotonic() if now is None else now;ids=self.registry.online(now,self.config.node_timeout)
        d,ages,edges=self.matrix.arrays(ids,now);t0=time.perf_counter();reason='';result=None
        # A node with fewer than d independent distances is not localizable;
        # do not let its single surviving link disable its measured neighbors.
        while len(ids)>self.config.dimensions+1:
            degree={node:sum(node in (r.a,r.b) for r in edges) for node in ids}
            weak={node for node,degree in degree.items() if degree<self.config.dimensions}
            if not weak or len(ids)-len(weak)<self.config.dimensions+1:break
            selected=[i for i,node in enumerate(ids) if node not in weak]
            ids=[ids[i] for i in selected];d=d[np.ix_(selected,selected)];ages=ages[np.ix_(selected,selected)]
            edges=[r for r in edges if r.a not in weak and r.b not in weak]
        # A missing node must not invalidate the connected measured component.
        remaining=set(ids);components=[]
        while remaining:
            component={remaining.pop()};front=list(component)
            while front:
                node=front.pop()
                peers={r.b if r.a==node else r.a for r in edges if node in (r.a,r.b)} & remaining
                remaining-=peers;component|=peers;front.extend(peers)
            components.append(component)
        if len(components)>1:
            chosen=max(components,key=len)
            if len(chosen)>=self.config.dimensions+1:
                selected=[i for i,node in enumerate(ids) if node in chosen]
                ids=[ids[i] for i in selected];d=d[np.ix_(selected,selected)];ages=ages[np.ix_(selected,selected)]
                edges=[r for r in edges if r.a in chosen and r.b in chosen]
        if self.matrix.generation!=self.last_generation:
            self.last_generation=self.matrix.generation
            try:
                with threadpool_limits(limits=1):result=self._solve(ids,d,edges,now,t0)
            except (ValueError,TimeoutError,np.linalg.LinAlgError) as exc:reason=str(exc)
            self.last_solve=(time.perf_counter()-t0)*1000;self.solve_times.append(self.last_solve)
        if result:
            points,meta=result
            if self.config.algorithm=='swarm':
                pass  # Weighted Kabsch is applied by the swarm solver itself.
            elif self.config.motion_profile and self.frame_reference is not None:
                common=[node for node in ids if node in self.frame_ids and node!=self.config.moving_node and (not self.config.reference_nodes or node in self.config.reference_nodes)]
                if len(common)>=3:
                    a=points[[ids.index(node) for node in common]]
                    b=self.frame_reference[[self.frame_ids.index(node) for node in common]]
                    if self.config.dimensions==2:
                        a=a[:,:2];b=b[:,:2];u,_,vt=np.linalg.svd((a-a.mean(0)).T@(b-b.mean(0)))
                        if np.linalg.det(u@vt)<0:u[:,-1]*=-1
                        points[:,:2]=(points[:,:2]-a.mean(0))@(u@vt)+b.mean(0);meta['reflection_aligned']=False
                    else:
                        u,_,vt=np.linalg.svd((a-a.mean(0)).T@(b-b.mean(0)));rot=u@vt
                        points=(points-a.mean(0))@rot+b.mean(0);meta['reflection_aligned']=bool(np.linalg.det(rot)<0)
                else:raise ValueError('Insufficient shared frame nodes')
            elif self.previous is not None and ids==self.previous_ids:
                points,meta['reflection_aligned']=align(points,self.previous)
            else:
                base=[i for i,node in enumerate(ids) if not self.config.reference_nodes or node in self.config.reference_nodes]
                points-=points[base].mean(0);self.filter.reset()
            if self.config.motion_profile and self.frame_reference is None:
                self.frame_reference=points.copy();self.frame_ids=list(ids)
            if ids!=self.previous_ids:self.filter.reset()
            if self.config.dimensions==2:points[:,2]=0
            self.previous=points;self.previous_ids=list(ids);self.accepted_at=now
            self.observed_at=max(r.effective for r in edges);self.accepts.append(now)
            shown=self.filter.update(points,now,max(self.config.noise_m,meta['rms'])) if self.config.filtering else points
            unavailable=meta.get('unavailable_nodes',{})
            self.last_solution=dict(meta,positions={n:p.tolist() for n,p in zip(ids,shown) if n not in unavailable},measured_positions={n:p.tolist() for n,p in zip(ids,points) if n not in unavailable})
            self.matrix.geometry={n:p.copy() for n,p in zip(ids,points) if n not in unavailable}
            self.matrix.geometry_stamp=now
            for i,node in enumerate(ids):
                if node in unavailable:continue
                self.node_cache[node]=(shown[i].copy(),self.filter.x[i,3:].copy() if self.filter.x is not None else np.zeros(3),now)
            reason=('Planar 2D estimate; Z=0 is the assumed table plane, not measured height' if self.config.dimensions==2 else 'Relative 3D estimate; translation, rotation and reflection unobservable')
        age=now-self.observed_at if self.observed_at is not None else math.inf
        state='unavailable';positions={}
        if self.last_solution and ids==self.previous_ids:
            positions=dict(self.last_solution['positions'])
            if result:state='measured-supported'
            elif age<=self.config.prediction_limit and self.config.filtering and self.filter.x is not None:
                positions={n:p.tolist() for n,p in zip(ids,self.filter.predict(now))};state='predicted'
            else:state='stale'
        if result and not self.last_solution.get('converged'):state='estimated'
        node_states={node:state for node in positions}
        for node in self.last_solution.get('unavailable_nodes',{}):node_states[node]='unavailable'
        for node,(point,velocity,stamp) in self.node_cache.items():
            if node in positions:continue
            elapsed=now-stamp
            node_states[node]='predicted' if self.config.filtering and elapsed<=self.config.prediction_limit else 'stale'
            positions[node]=(point+velocity*elapsed if node_states[node]=='predicted' else point).tolist()
        fresh_ages=[now-r.effective for r in edges]
        return dict(source='unset',time=now,ids=ids,dimensions=self.config.dimensions,nodes=[asdict(n) for n in self.registry.nodes.values()],
            positions=positions,node_states=node_states,measured_positions=self.last_solution.get('measured_positions',{}),state=state,reason=reason or self.last_solution.get('message','Waiting for ranges'),
            age=age if math.isfinite(age) else None,edges=[asdict(r) for r in edges],
            matrix=[[float(v) if math.isfinite(v) else None for v in row] for row in d],
            ages=[[float(v) if math.isfinite(v) else None for v in row] for row in ages],
            metrics=dict(valid_edges=len(edges),possible_edges=len(ids)*(len(ids)-1)//2,stale_edges=sum(now-r.effective>self.config.max_age for r in self.matrix.links.values()),
                range_filtering=self.config.range_filtering,range_spikes_rejected=sum(f.rejected for f in self.matrix.filters.values()),
                inconsistent_links=getattr(self.matrix,'inconsistent',[]),
                motion_profile=self.config.motion_profile,moving_node=self.config.moving_node,
                reference_nodes=list(self.config.reference_nodes),node_quality=self.last_solution.get('node_quality',{}),unavailable_nodes=self.last_solution.get('unavailable_nodes',{}),
                rms_scope='reference group' if self.config.reference_nodes else 'whole graph',
                mean_age=sum(fresh_ages)/len(fresh_ages) if fresh_ages else None,max_age=max(fresh_ages,default=None),
                success_rate=self.matrix.success/self.matrix.attempts if self.matrix.attempts else None,
                solver_ms=self.last_solve,localization_hz=sum(now-t<1 for t in self.accepts),
                rms=self.last_solution.get('rms'),condition=self.last_solution.get('condition'),
                measurement_span=(max(r.effective for r in edges)-min(r.effective for r in edges)) if edges else None),
            uncertainty=self.last_solution.get('uncertainty'),velocity={n:v.tolist() for n,v in zip(ids,self.filter.x[:,3:])} if ids==self.previous_ids and self.filter.x is not None else {})

    def _solve(self,ids,d,edges,now,t0):
        base=[node for node in ids if node in self.config.reference_nodes]
        if self.config.dimensions!=2 or len(base)<3 or not self.config.reference_nodes:
            return self._solve_graph(ids,d,edges,now,t0)
        ix=[ids.index(node) for node in base];base_edges=[r for r in edges if r.a in base and r.b in base]
        anchors,meta=self._solve_graph(base,d[np.ix_(ix,ix)],base_edges,now,t0)
        points=np.zeros((len(ids),3))
        if self.previous is not None and all(node in self.previous_ids for node in base):
            old=self.previous[[self.previous_ids.index(node) for node in base],:2];new=anchors[:,:2]
            u,_,vt=np.linalg.svd((old-old.mean(0)).T@(new-new.mean(0)))
            if np.linalg.det(u@vt)<0:u[:,-1]*=-1
            for i,node in enumerate(ids):
                if node in self.previous_ids:points[i,:2]=(self.previous[self.previous_ids.index(node),:2]-old.mean(0))@(u@vt)+new.mean(0)
        points[ix]=anchors
        quality={};unavailable={}
        for node in ids:
            if node in base:continue
            observed=[r for r in edges if node in (r.a,r.b) and (r.b if r.a==node else r.a) in base]
            required=min(4,len(base))
            if len(observed)<required:
                unavailable[node]=f'Waiting for {required} fresh reference ranges';continue
            a=np.array([anchors[base.index(r.b if r.a==node else r.a),:2] for r in observed])
            distances=np.array([r.distance for r in observed])
            matrix=2*(a[1:]-a[0]);rhs=distances[0]**2-distances[1:]**2+np.sum(a[1:]**2,axis=1)-a[0]@a[0]
            if np.linalg.matrix_rank(matrix)<2:
                unavailable[node]='Reference geometry collinear';continue
            linear=np.linalg.lstsq(matrix,rhs,rcond=None)[0]
            def residual(x):
                if (time.perf_counter()-t0)*1000>self.config.solver_budget_ms:raise TimeoutError('Solver time budget exceeded')
                return np.linalg.norm(x-a,axis=1)-distances
            # Algebraic seed and its reflection avoid retaining a distant node
            # in the wrong local solution after it crosses the reference group.
            _,_,vt=np.linalg.svd(a-a.mean(0));normal=vt[-1]
            reflected=linear-2*((linear-a.mean(0))@normal)*normal
            prior=None
            if self.previous is not None and all(n in self.previous_ids for n in [*base,node]) and node in self.node_cache:
                old=self.previous[[self.previous_ids.index(n) for n in base],:2];new=anchors[:,:2]
                u,_,vt=np.linalg.svd((old-old.mean(0)).T@(new-new.mean(0)))
                if np.linalg.det(u@vt)<0:u[:,-1]*=-1
                prior=(self.previous[self.previous_ids.index(node),:2]-old.mean(0))@(u@vt)+new.mean(0)
            seeds=[linear,reflected]+([prior] if prior is not None else [])
            fits=[least_squares(residual,seed,loss='soft_l1',f_scale=self.config.robust_scale_m,max_nfev=25) for seed in seeds]
            fit=min(fits,key=lambda fit:fit.cost)
            if prior is not None:
                elapsed=max(.01,now-self.node_cache[node][2]);limit=.20+3.*min(elapsed,.15)
                nearby=[f for f in fits if np.linalg.norm(f.x-prior)<=limit and np.sqrt(np.mean(residual(f.x)**2))<=self.config.max_rms_m]
                if nearby:
                    fit=min(nearby,key=lambda f:f.cost);self.reacquire.pop(node,None)
                else:
                    # Don't flip across the reference baseline on a single
                    # ambiguous frame. Clean persistent new solutions reacquire.
                    rms=float(np.sqrt(np.mean(residual(fit.x)**2)))
                    previous,count=self.reacquire.get(node,(fit.x,0))
                    count=count+1 if rms<=self.config.max_rms_m and np.linalg.norm(fit.x-previous)<.15 else 0
                    self.reacquire[node]=(fit.x.copy(),count)
                    if count<5:
                        unavailable[node]='Ambiguous position jump; waiting for consistent ranges'
                        points[ids.index(node),:2]=prior;continue
            errors=residual(fit.x)
            rms=float(np.sqrt(np.mean(errors**2)))
            delta=fit.x-a;unit=delta/np.maximum(np.linalg.norm(delta,axis=1)[:,None],1e-6)
            eigen=np.linalg.eigvalsh(unit.T@unit)
            uncertainty=float(max(rms,self.config.noise_m)/math.sqrt(max(eigen[0],1e-12)))
            quality[node]=dict(rms=rms,uncertainty=uncertainty,geometry_condition=float(eigen[-1]/max(eigen[0],1e-12)),references=len(observed))
            if rms>self.config.max_rms_m or uncertainty>self.config.max_uncertainty_m:
                unavailable[node]=f'Weak/inconsistent ranges: RMS {rms:.3f}m, conditional uncertainty {uncertainty:.3f}m';continue
            points[ids.index(node),:2]=fit.x
        meta.update(node_quality=quality,unavailable_nodes=unavailable)
        return points,meta

    def _solve_graph(self,ids,d,edges,now,t0):
        n=len(ids);dim=self.config.dimensions
        if n<dim+1:raise ValueError(f'Fewer than {dim+1} nodes: ranges only; {dim}D unavailable')
        if len(edges)<dim*n-dim*(dim+1)//2:raise ValueError('Insufficient fresh constraints')
        complete=np.all(np.isfinite(d))
        graph=np.where(np.isfinite(d),d,np.inf);paths=shortest_path(graph,directed=False)
        if not np.all(np.isfinite(paths)):raise ValueError('Disconnected graph: common 3D frame unavailable')
        if dim==2:
            if complete:initial=mds(d,2)
            elif self.previous is not None and ids==self.previous_ids:initial=self.previous
            else:initial=planar_lateration(d)
        else:initial=mds(d) if complete else lateration(d)
        previous_subset=self.previous is not None and all(node in self.previous_ids for node in ids)
        if previous_subset:seed=canonical(self.previous[[self.previous_ids.index(node) for node in ids]],dim)
        else:seed=canonical(initial,dim)
        index={id:i for i,id in enumerate(ids)};a=np.array([index[r.a] for r in edges]);b=np.array([index[r.b] for r in edges])
        distances=np.array([r.distance for r in edges]);lag=np.array([max(0,now-r.effective) for r in edges])
        weights=np.array([(.2 if r.nlos else 1.)*(r.quality if r.quality is not None else 1.) for r in edges])
        weights=np.sqrt(np.maximum(.05,weights)*np.exp(-lag/self.config.max_age))
        free=np.array([3*i+k for i in range(n) for k in range(dim) if i!=0 and not(i==1 and k>0) and not(i==2 and k==2)])
        deadline=t0+self.config.solver_budget_ms/1000
        velocity=np.zeros((n,3))
        if not self.config.motion_profile and ids==self.previous_ids and self.filter.x is not None:
            # Express relative velocity in the same temporary gauge as seed.
            u,_,vt=np.linalg.svd((self.previous-self.previous.mean(0)).T@(seed-seed.mean(0)))
            velocity=self.filter.x[:,3:]@(u@vt)
        def unpack(x):p=seed.copy();p.flat[free]=x;return p
        def raw(p):
            delta=p[a]-p[b]-(velocity[a]-velocity[b])*lag[:,None]
            length=np.maximum(np.linalg.norm(delta,axis=1),1e-9)
            return length-distances,delta/length[:,None]
        temporal=self.config.temporal_weight if previous_subset else 0.
        target=seed.flatten()[free]
        def fun(x):
            if time.perf_counter()>deadline:raise TimeoutError('Solver time budget exceeded')
            errors,_=raw(unpack(x));values=errors*weights
            return np.r_[values,math.sqrt(temporal)*(x-target)] if temporal else values
        def jac(x):
            _,unit=raw(unpack(x));j=np.zeros((len(edges),n*3));rows=np.arange(len(edges))[:,None]
            j[rows,a[:,None]*3+np.arange(3)]=unit;j[rows,b[:,None]*3+np.arange(3)]=-unit
            j=j[:,free]*weights[:,None]
            return np.vstack((j,np.eye(len(free))*math.sqrt(temporal))) if temporal else j
        fit=least_squares(fun,seed.flatten()[free],jac=jac,loss='huber',f_scale=self.config.robust_scale_m,
                          max_nfev=self.config.max_evaluations,ftol=1e-4,xtol=1e-5,gtol=1e-5)
        points=unpack(fit.x);errors,_=raw(points);rms=float(np.sqrt(np.mean(errors**2)))
        # Check RANGE-only Jacobian, excluding temporal regularization.
        j=jac(fit.x)[:len(edges)];sv=np.linalg.svd(j,compute_uv=False)
        shape=np.linalg.svd(points-points.mean(0),compute_uv=False)
        if shape[dim-1]<shape[0]*.025:raise ValueError('Nearly collinear: XY is not trustworthy' if dim==2 else 'Nearly coplanar: Z is not trustworthy')
        if len(sv)<len(free) or sv[-1]<sv[0]*1e-3:raise ValueError(f'Rank-deficient or weak {dim}D rigidity')
        if rms>self.config.max_rms_m:raise ValueError(f'Inconsistent ranges: RMS {rms:.3f} m')
        # Conditional covariance in the relative gauge, not absolute certainty.
        variance=np.diag(np.linalg.pinv(j.T@j))*max(rms,self.config.noise_m)**2
        uncertainty=float(np.sqrt(max(variance)))
        if uncertainty>self.config.max_uncertainty_m:
            raise ValueError(f'Weak {dim}D geometry: conditional uncertainty {uncertainty:.2f} m exceeds limit')
        if dim==3 and shape[-1]/math.sqrt(n)<2*uncertainty:
            raise ValueError('Nearly coplanar within range uncertainty: Z is not trustworthy')
        return points,dict(rms=rms,condition=float(sv[0]/sv[-1]),uncertainty=uncertainty,
            converged=bool(fit.success),message=str(fit.message),reflection_aligned=False,
            observability=('planar assumption; height not measured' if dim==2 else 'noncoplanar lateration scaffold' if not complete else 'complete relative graph'))
