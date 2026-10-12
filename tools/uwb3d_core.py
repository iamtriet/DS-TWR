"""Transport-independent relative 3D estimation. No fixed physical anchors."""
import itertools
import math
import time
from dataclasses import dataclass,asdict
from collections import deque
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse.csgraph import shortest_path
from threadpoolctl import threadpool_limits
from uwb3d_protocol import node_id


@dataclass
class Config:
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
    imu_motion_gating:bool=True
    gateway_slot_ms:int=10
    gateway_superframe_ms:int=500
    gateway_network_id:int=0x43594231
    reference_period_ms:int=200
    reference_tag:int=0
    reference_anchor_mask:int=0
    def validate(self):
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


class Matrix:
    def __init__(self,config):self.config=config;self.links={};self.history={};self.attempts=0;self.success=0;self.generation=0
    def update(self,r):
        node_id(r.a);node_id(r.b)
        if r.a==r.b or not all(math.isfinite(x) for x in (r.effective,r.received)):raise ValueError('range identity/time')
        if r.effective>r.received+.01:raise ValueError('Measurement in future')
        key=tuple(sorted((r.a,r.b)));self.attempts+=1
        previous=self.links.get(key)
        if previous:
            delta=(r.sequence-previous.sequence)&0xffffffff
            if delta==0 or delta>=0x80000000 or r.effective<previous.effective:return False
        if not r.valid:return False
        if not math.isfinite(r.distance) or not 0<r.distance<=1000:raise ValueError('range bounds')
        self.success+=1;self.generation+=1;self.links[key]=r
        self.history.setdefault(key,deque(maxlen=120)).append((r.effective,r.distance))
        return True
    def arrays(self,ids,now):
        n=len(ids);d=np.full((n,n),np.nan);age=np.full((n,n),np.inf);np.fill_diagonal(d,0.);np.fill_diagonal(age,0.)
        index={id:i for i,id in enumerate(ids)};edges=[]
        for (a,b),r in self.links.items():
            if a not in index or b not in index:continue
            i,j=index[a],index[b];age[i,j]=age[j,i]=max(0.,now-r.effective)
            if age[i,j]<=self.config.max_age:
                d[i,j]=d[j,i]=r.distance;edges.append(r)
        return d,age,edges


def align(points,reference):
    """O(3), including reflection: ranges cannot distinguish chirality."""
    a=points-points.mean(0);b=reference-reference.mean(0)
    u,_,vt=np.linalg.svd(a.T@b);rot=u@vt
    return a@rot+reference.mean(0),bool(np.linalg.det(rot)<0)


def mds(distances,dimensions=3):
    if not np.all(np.isfinite(distances)):raise ValueError('MDS requires complete distances')
    n=len(distances);h=np.eye(n)-np.ones((n,n))/n
    vals,vec=np.linalg.eigh(-.5*h@(distances**2)@h)
    order=np.argsort(vals)[::-1][:dimensions]
    if len(order)<dimensions or vals[order[-1]]<max(vals[order[0]],1e-9)*1e-5:raise ValueError(f'Insufficient {dimensions}D geometry')
    points=np.zeros((n,3));points[:,:dimensions]=vec[:,order]*np.sqrt(np.maximum(vals[order],0))
    return points


def canonical(points):
    p=points-points[0];x=p[1]/np.linalg.norm(p[1]);z=np.cross(x,p[2]);zn=np.linalg.norm(z)
    if zn<1e-6:raise ValueError('Reference triplet is collinear')
    z/=zn;y=np.cross(z,x)
    return p@np.stack((x,y,z),axis=1)


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
            for i in range(n):
                p=f@self.cov[i]@f.T+np.eye(6)*.2*dt
                innovation=positions[i]-predicted[i,:3];s=p[:3,:3]+np.eye(3)*sigma*sigma
                if innovation@np.linalg.solve(s,innovation)>25:
                    predicted[i,:3]=positions[i];predicted[i,3:]=0;p=np.eye(6)*.1
                else:
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
        self.last_generation=-1;self.last_solution={};self.solve_times=deque(maxlen=100);self.last_solve=0.;self.accepts=deque(maxlen=100)
    def reset(self):
        self.previous=None;self.filter.reset();self.accepted_at=None;self.observed_at=None;self.last_generation=-1;self.last_solution={}
    def ingest(self,r):
        if r.a not in self.registry.nodes or r.b not in self.registry.nodes:raise ValueError('Range from unregistered node')
        for n in (r.a,r.b):self.registry.nodes[n].last=r.received
        return self.matrix.update(r)
    def estimate(self,now=None):
        now=time.monotonic() if now is None else now;ids=self.registry.online(now,self.config.node_timeout)
        d,ages,edges=self.matrix.arrays(ids,now);t0=time.perf_counter();reason='';result=None
        if self.matrix.generation!=self.last_generation:
            self.last_generation=self.matrix.generation
            try:
                with threadpool_limits(limits=1):result=self._solve(ids,d,edges,now,t0)
            except (ValueError,TimeoutError,np.linalg.LinAlgError) as exc:reason=str(exc)
            self.last_solve=(time.perf_counter()-t0)*1000;self.solve_times.append(self.last_solve)
        if result:
            points,meta=result
            if self.previous is not None and ids==self.previous_ids:points,meta['reflection_aligned']=align(points,self.previous)
            else:points-=points.mean(0);self.filter.reset()
            if self.config.dimensions==2:points[:,2]=0
            self.previous=points;self.previous_ids=list(ids);self.accepted_at=now
            self.observed_at=max(r.effective for r in edges);self.accepts.append(now)
            shown=self.filter.update(points,now,max(self.config.noise_m,meta['rms'])) if self.config.filtering else points
            self.last_solution=dict(meta,positions={n:p.tolist() for n,p in zip(ids,shown)},measured_positions={n:p.tolist() for n,p in zip(ids,points)})
            reason=('Planar 2D estimate; Z=0 is the assumed table plane, not measured height' if self.config.dimensions==2 else 'Relative 3D estimate; translation, rotation and reflection unobservable')
        age=now-self.observed_at if self.observed_at is not None else math.inf
        state='unavailable';positions={}
        if self.last_solution and ids==self.previous_ids:
            positions=self.last_solution['positions']
            if result:state='measured-supported'
            elif age<=self.config.prediction_limit and self.config.filtering and self.filter.x is not None:
                positions={n:p.tolist() for n,p in zip(ids,self.filter.predict(now))};state='predicted'
            else:state='stale'
        if result and not self.last_solution.get('converged'):state='estimated'
        fresh_ages=[now-r.effective for r in edges]
        return dict(source='unset',time=now,ids=ids,dimensions=self.config.dimensions,nodes=[asdict(n) for n in self.registry.nodes.values()],
            positions=positions,measured_positions=self.last_solution.get('measured_positions',{}),state=state,reason=reason or self.last_solution.get('message','Waiting for ranges'),
            age=age if math.isfinite(age) else None,edges=[asdict(r) for r in edges],
            matrix=[[float(v) if math.isfinite(v) else None for v in row] for row in d],
            ages=[[float(v) if math.isfinite(v) else None for v in row] for row in ages],
            metrics=dict(valid_edges=len(edges),possible_edges=len(ids)*(len(ids)-1)//2,stale_edges=sum(now-r.effective>self.config.max_age for r in self.matrix.links.values()),
                mean_age=sum(fresh_ages)/len(fresh_ages) if fresh_ages else None,max_age=max(fresh_ages,default=None),
                success_rate=self.matrix.success/self.matrix.attempts if self.matrix.attempts else None,
                solver_ms=self.last_solve,localization_hz=sum(now-t<1 for t in self.accepts),
                rms=self.last_solution.get('rms'),condition=self.last_solution.get('condition'),
                measurement_span=(max(r.effective for r in edges)-min(r.effective for r in edges)) if edges else None),
            uncertainty=self.last_solution.get('uncertainty'),velocity={n:v.tolist() for n,v in zip(ids,self.filter.x[:,3:])} if ids==self.previous_ids and self.filter.x is not None else {})

    def _solve(self,ids,d,edges,now,t0):
        n=len(ids);dim=self.config.dimensions
        if n<dim+1:raise ValueError(f'Fewer than {dim+1} nodes: ranges only; {dim}D unavailable')
        if len(edges)<dim*n-dim*(dim+1)//2:raise ValueError('Insufficient fresh constraints')
        complete=np.all(np.isfinite(d))
        graph=np.where(np.isfinite(d),d,np.inf);paths=shortest_path(graph,directed=False)
        if not np.all(np.isfinite(paths)):raise ValueError('Disconnected graph: common 3D frame unavailable')
        if dim==2:
            if complete:initial=mds(d,2)
            elif self.previous is not None and ids==self.previous_ids:initial=self.previous
            else:raise ValueError('Planar startup requires all pair distances; waiting for neighbor survey')
        else:initial=mds(d) if complete else lateration(d)
        if self.previous is not None and ids==self.previous_ids:seed=canonical(self.previous)
        else:seed=canonical(initial)
        index={id:i for i,id in enumerate(ids)};a=np.array([index[r.a] for r in edges]);b=np.array([index[r.b] for r in edges])
        distances=np.array([r.distance for r in edges]);lag=np.array([max(0,now-r.effective) for r in edges])
        weights=np.array([(.2 if r.nlos else 1.)*(r.quality if r.quality is not None else 1.) for r in edges])
        weights=np.sqrt(np.maximum(.05,weights)*np.exp(-lag/self.config.max_age))
        free=np.array([3*i+k for i in range(n) for k in range(dim) if i!=0 and not(i==1 and k>0) and not(i==2 and k==2)])
        deadline=t0+self.config.solver_budget_ms/1000
        velocity=np.zeros((n,3))
        if ids==self.previous_ids and self.filter.x is not None:
            # Express relative velocity in the same temporary gauge as seed.
            u,_,vt=np.linalg.svd((self.previous-self.previous.mean(0)).T@(seed-seed.mean(0)))
            velocity=self.filter.x[:,3:]@(u@vt)
        def unpack(x):p=seed.copy();p.flat[free]=x;return p
        def raw(p):
            delta=p[a]-p[b]-(velocity[a]-velocity[b])*lag[:,None]
            length=np.maximum(np.linalg.norm(delta,axis=1),1e-9)
            return length-distances,delta/length[:,None]
        temporal=self.config.temporal_weight if self.previous is not None and ids==self.previous_ids else 0.
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
