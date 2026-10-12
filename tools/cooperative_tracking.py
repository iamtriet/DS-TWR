"""Sparse cooperative distance graph with centralized, serialized UWB slots.

Relative coordinates only. A full-rank weighted Jacobian is required; damping,
old coordinates and IMU motion hints never substitute for fresh observations.
"""
import asyncio
import itertools
import math
import time
from collections import deque
from types import SimpleNamespace
import numpy as np
from auto_anchor import AutoAnchorFleet, relative_layout
from multi_anchor import MultiAnchorFleet
from tracking_estimation import Motion, TrackingEstimate


def edge(a,b): return tuple(sorted((a,b)))


class Link:
    def __init__(self):
        self.samples=deque(maxlen=12)
        self.reliability=.5
        self.attempted=0.
        self.residual=0.

    def update(self,distance,stamp):
        self.attempted=stamp
        self.reliability=.8*self.reliability+.2*(distance is not None)
        if distance is not None and math.isfinite(distance) and 0<distance<=1000:
            self.samples.append((distance,stamp))

    @property
    def sigma(self):
        if len(self.samples)<3: return .05
        data=np.array([d for d,_ in self.samples])
        return float(np.clip(1.4826*np.median(np.abs(data-np.median(data))),.03,.25))

    def score(self):
        return self.reliability/(self.sigma+.05+min(.3,self.residual))


def initialize(ids, links, selected, dimensions):
    """Range-only lateration seed; never presented without joint refinement."""
    points={ids[0]:np.zeros(dimensions)}
    distance=lambda a,b: links[edge(a,b)].samples[-1][0]
    d01=distance(ids[0],ids[1]);d02=distance(ids[0],ids[2]);d12=distance(ids[1],ids[2])
    x=(d01*d01+d02*d02-d12*d12)/(2*d01)
    y2=d02*d02-x*x
    if d01<.05 or y2<.0025: raise ValueError('First three anchors need a wider triangle')
    points[ids[1]]=np.array([d01]+[0.]*(dimensions-1))
    points[ids[2]]=np.array([x,math.sqrt(y2)]+([0.] if dimensions==3 else []))
    if dimensions==3:
        distances=[distance(a,b) for a,b in itertools.combinations(ids[:4],2)]
        points.update({n:np.array(p) for n,p in zip(ids[:4],relative_layout(distances))})
    for node in ids[len(points):]:
        peers=[p for p in points if edge(node,p) in selected and links.get(edge(node,p)) and links[edge(node,p)].samples]
        if len(peers)<dimensions+1: raise ValueError(f'{node}: waiting for enough independent neighbors')
        p0=points[peers[0]];d0=distance(node,peers[0])
        mat=[2*(points[p]-p0) for p in peers[1:]]
        rhs=[d0*d0-distance(node,p)**2+np.dot(points[p],points[p])-np.dot(p0,p0) for p in peers[1:]]
        if np.linalg.matrix_rank(mat)<dimensions: raise ValueError('Neighbor geometry is degenerate')
        points[node]=np.linalg.lstsq(mat,rhs,rcond=None)[0]
    return np.array([points[n] for n in ids])


def fit_graph(ids, measurements, dimensions, seed):
    """Huber IRLS + damped Gauss-Newton, jointly solving anchors AND tags."""
    coords=np.array(seed,dtype=float,copy=True)
    free=[(i,k) for i in range(len(ids)) for k in range(dimensions)
          if i!=0 and not (i==1 and k>0) and not (dimensions==3 and i==2 and k==2)]
    index={n:i for i,n in enumerate(ids)}
    if len(measurements)<len(free)+1: raise ValueError('Sparse graph lacks redundant range constraints')
    def system(points):
        jac=[]; errors=[]; weights=[]
        for (a,b),distance,sigma,quality,stamp in measurements:
            i,j=index[a],index[b];delta=points[i]-points[j];predicted=float(np.linalg.norm(delta))
            if predicted<1e-5: raise ValueError('Overlapping nodes in graph estimate')
            r=predicted-distance
            jac.append([delta[k]/predicted*(1 if n==i else -1 if n==j else 0) for n,k in free])
            errors.append(r)
            weights.append(max(.1,quality)/(sigma*sigma)*min(1.,.08/max(abs(r),1e-9)))
        return np.array(jac),np.array(errors),np.array(weights)
    def loss(points):
        _,r,_=system(points)
        base=np.array([max(.1,m[3])/(m[2]*m[2]) for m in measurements])
        ar=np.abs(r)
        return float(np.sum(base*np.where(ar<=.08,.5*r*r,.08*(ar-.04))))
    for _ in range(30):
        jac,r,w=system(coords)
        normal=jac.T@(w[:,None]*jac)
        step=np.linalg.solve(normal+np.diag(np.maximum(np.diag(normal),1.)*.001),-jac.T@(w*r))
        current=loss(coords);changed=False
        for gain in (1.,.5,.25,.125,.0625):
            proposed=coords.copy()
            for (i,k),delta in zip(free,step): proposed[i,k]+=gain*delta
            if loss(proposed)<=current:
                coords=proposed;changed=True;break
        if not changed or np.linalg.norm(step)<1e-5: break
    jac,r,w=system(coords)
    singular=np.linalg.svd(np.sqrt(w)[:,None]*jac,compute_uv=False)
    if len(singular)<len(free) or singular[-1]<singular[0]*1e-3:
        raise ValueError('Sparse graph is not observable or geometry is too weak')
    rms=float(np.sqrt(np.mean(r*r)))
    if not np.all(np.isfinite(coords)) or rms>.08 or np.max(np.abs(r))>.15:
        raise ValueError(f'Cooperative ranges inconsistent: RMS {rms*100:.1f} cm, peak {np.max(np.abs(r))*100:.1f} cm')
    if coords[1,0]<0: coords[:,0]*=-1
    if coords[2,1]<0: coords[:,1]*=-1
    if dimensions==3 and coords[3,2]<0: coords[:,2]*=-1
    return coords,rms,r,float(singular[0]/singular[-1])


class CooperativeGraph:
    def __init__(self,plan,mode='xy',clock=time.monotonic):
        self.plan,self.mode,self.clock=plan,mode,clock
        self.dimensions=2 if mode=='xy' else 3
        self.boards=plan['anchors']+plan['tags'];self.ids=[b['id'] for b in self.boards]
        self.links={};self.positions={};self.selected=set();self.neighbors={}
        self.motion={b['name']:Motion() for b in self.boards}
        self.invalid_after=0.;self.round=0;self.last_frame=None
        self.cycle_durations=deque(maxlen=8)
        self.reason='Collecting the initial cooperative graph'

    def feed(self,board,line,stamp):
        if board not in self.motion: return
        m=self.motion[board]
        if m.feed(line,stamp) and board in {b['name'] for b in self.plan['anchors']}:
            if m.last is not None and m.quality>=2 and (m.acc>.5 or m.gyro>.15):
                self.invalid_after=stamp

    def reset_links(self):
        self.links.clear();self.invalid_after=self.clock()

    def update(self,a,b,distance,stamp):
        self.links.setdefault(edge(a,b),Link()).update(distance,stamp)

    def choose(self):
        anchors=[b['id'] for b in self.plan['anchors']]
        previous=self.neighbors
        self.selected=set(itertools.combinations(anchors,2));self.neighbors={}
        predecessors=list(anchors)
        for tag in self.plan['tags']:
            node=tag['id'];count=self.dimensions+1
            options=list(itertools.combinations(predecessors,count))
            def value(peers):
                quality=sum(self.links.get(edge(node,p),Link()).score() for p in peers)/count
                spread=1.
                if all(p in self.positions for p in peers):
                    pts=np.array([self.positions[p][:self.dimensions] for p in peers])
                    if node in self.positions:
                        bearings=pts-np.array(self.positions[node][:self.dimensions])
                        bearings/=np.maximum(np.linalg.norm(bearings,axis=1)[:,None],.01)
                        sv=np.linalg.svd(bearings,compute_uv=False)
                    else: sv=np.linalg.svd(pts[1:]-pts[0],compute_uv=False)
                    spread=max(.01,float(sv[-1]/max(sv[0],.001)))
                # Prefer a usable cooperative tag edge, never force a broken link.
                cooperative=.5 if any(p.startswith('B') and self.links.get(edge(node,p),Link()).reliability>.3 for p in peers) else 0.
                retained=.12*sum(p in previous.get(node,[]) for p in peers)
                weakest=min(self.links.get(edge(node,p),Link()).reliability for p in peers)
                return (quality*(.2+spread)+cooperative+retained)*min(1.,weakest/.35)
            peers=max(options,key=value)
            self.neighbors[node]=list(peers)
            self.selected.update(edge(node,p) for p in peers)
            predecessors.append(node)
        return self.selected

    def ttl(self,key): return 4.5 if all(n.startswith('A') for n in key) else 2.5

    def fresh(self,key,now):
        link=self.links.get(key)
        return bool(link and link.samples and link.samples[-1][1]>=self.invalid_after and now-link.samples[-1][1]<=self.ttl(key))

    def schedule(self):
        self.choose();now=self.clock()
        backbone=sorted(k for k in self.selected if all(n.startswith('A') for n in k))
        tag_edges=sorted(self.selected-set(backbone))
        # Bootstrap/recovery/motion: restore all stale structural edges first.
        # Refresh edges that would expire before this cycle finishes, not just
        # those already stale at its start. Keep the rolling refresh progressing.
        budget=(max(self.cycle_durations) if self.cycle_durations else 1.5)+.15
        urgent=[k for k in backbone if not self.fresh(k,now+budget)]
        oldest=sorted(backbone,key=lambda k:self.links[k].samples[-1][1]
                      if k in self.links and self.links[k].samples else 0.)[:2]
        refresh=list(dict.fromkeys(urgent+oldest))
        slots=refresh+tag_edges
        # One rotating exploration slot prevents a rejected neighbor staying
        # excluded forever. No concurrent ranges and a bounded retry budget.
        alternatives=sorted(set(itertools.combinations(self.ids,2))-self.selected)
        if alternatives and self.round%3==0:
            candidate=min(alternatives,key=lambda k:self.links.get(k,Link()).attempted)
            slots.append(candidate)
        self.round+=1
        return list(dict.fromkeys(slots))

    def solve(self):
        now=self.clock();measurements=[]
        try:
            for k in sorted(self.selected):
                if not self.fresh(k,now): raise ValueError('Waiting for fresh selected neighbor ranges')
                link=self.links[k];d,stamp=link.samples[-1]
                measurements.append((k,d,link.sigma,link.reliability,stamp))
            if self.positions:
                seed=np.array([self.positions[n][:self.dimensions] for n in self.ids])
                try:
                    fit=fit_graph(self.ids,measurements,self.dimensions,seed)
                except (ValueError,np.linalg.LinAlgError):
                    # A changed neighbor graph can trap the previous solution
                    # in a wrong local minimum. Re-laterate from fresh ranges;
                    # the same rank and residual gates still apply.
                    seed=initialize(self.ids,self.links,self.selected,self.dimensions)
                    fit=fit_graph(self.ids,measurements,self.dimensions,seed)
            else:
                seed=initialize(self.ids,self.links,self.selected,self.dimensions)
                fit=fit_graph(self.ids,measurements,self.dimensions,seed)
            coords,rms,residuals,condition=fit
            self.positions={n:list(map(float,p))+([0.] if self.dimensions==2 else []) for n,p in zip(self.ids,coords)}
            for m,r in zip(measurements,residuals): self.links[m[0]].residual=abs(float(r))
            positions={t['id']:self.positions[t['id']] for t in self.plan['tags']}
            self.reason=f'Cooperative fix · {len(measurements)} active edges · fit RMS {100*rms:.1f} cm (not accuracy)'
            self.last_frame=dict(profile=dict(mode=self.mode,anchors={b['name']:self.positions[b['id']] for b in self.plan['anchors']}),
                positions=positions,rms=rms,condition=condition,stamp=min(m[4] for m in measurements),
                expires=min(m[4]+self.ttl(m[0]) for m in measurements),
                tag_stamps={t['id']:min(m[4] for m in measurements if t['id'] in m[0]) for t in self.plan['tags']})
            valid=True
        except (ValueError,KeyError,np.linalg.LinAlgError) as exc:
            self.reason=str(exc);valid=False
        return dict(valid=valid,reason=self.reason,frame=self.last_frame,neighbors=self.neighbors,
                    selected=[list(k) for k in sorted(self.selected)],
                    edges=[dict(a=a,b=b,distance=l.samples[-1][0] if l.samples else None,
                                stamp=l.samples[-1][1] if l.samples else None,reliability=l.reliability)
                           for (a,b),l in self.links.items()])


class CooperativeFleet(AutoAnchorFleet):
    def __init__(self,plan,emit,factory=None,mode='xy'):
        from control_backend import Session
        self.outer_emit=emit;self.graph=CooperativeGraph(plan,mode)
        super().__init__(plan,self.observe,factory or Session)

    def observe(self,kind,value):
        if kind=='line': self.graph.feed(*value)
        elif kind=='auto_reconnecting': self.graph.reset_links()
        self.outer_emit(kind,value)

    async def configure(self,board,is_tag):
        # Reuse identity verification/IMU init; then enable symmetric ranging.
        await MultiAnchorFleet.configure(self,board,is_tag)
        session=self.connections[board['name']]
        await session.command('uwb stop')
        await session.command('uwb anchors '+' '.join(b['id'] for b in self.graph.boards if b['id']!=board['id']))
        await session.command('uwb cooperate')

    async def cycle(self,samples=1):
        await self.ensure_connections()
        started=time.monotonic();slots=self.graph.schedule()
        by_id={b['id']:b for b in self.graph.boards}
        for number,(a,b) in enumerate(slots):
            if self.stopping: return
            # A single awaited command owns the radio slot until ACK/timeout.
            distance,stamp=await self.measure(by_id[a],by_id[b])
            self.graph.update(a,b,distance,stamp)
            self.outer_emit('coop_slot',dict(slot=number,a=a,b=b,distance=distance,stamp=stamp))
        result=self.graph.solve()
        duration=time.monotonic()-started
        self.graph.cycle_durations.append(duration)
        result.update(duration=duration,slots=len(slots))
        self.outer_emit('coop_frame',result)


class CooperativeDisplay(TrackingEstimate):
    automatic=True
    cooperative=True
    def __init__(self,plan,mode='xy',clock=time.monotonic):
        super().__init__(plan,clock);self.mode=mode;self.reason='Waiting for cooperative ranges'
        self.results={};self.last_display=None;self.last_epoch=None;self.neighbors={};self.selected=[];self.edges=[]
    def invalidate(self,reason): self.profile=None;self.reason=reason
    def feed(self,board,line,stamp):
        matched=super().feed(board,line,stamp)
        if matched and board in {b['name'] for b in self.plan['anchors']}:
            m=self.motion[board]
            if m.last is not None and m.quality>=2 and (m.acc>.5 or m.gyro>.15):
                self.invalidate('Anchor motion detected; refreshing cooperative graph')
        return matched
    def accept(self,value):
        self.reason=value['reason'];self.neighbors=value['neighbors'];self.selected=value['selected'];self.edges=value['edges']
        f=value.get('frame')
        if not value['valid'] or not f: self.invalidate(self.reason);return
        self.profile=f['profile'];self.results={t:(xyz,f['rms']) for t,xyz in f['positions'].items()}
        self.expires=f['expires'];results=dict(self.results)
        # Tag stamps are observation ages, never invented anchor/tag distances.
        stamps=f['tag_stamps']
        def locate(tag):
            if tag not in results: raise ValueError('No cooperative fix for tag')
            return results[tag]
        self.last_display=SimpleNamespace(automatic=True,cooperative=True,plan=self.plan,profile=self.profile,
            results=results,locate=locate,tag_stamps=stamps,selected=self.selected,
            positions={**{b['id']:self.profile['anchors'][b['name']] for b in self.plan['anchors']},**f['positions']},
            stamp=f['stamp'],held=False,age=0.)
    def expire(self):
        if self.profile and self.clock()>self.expires:self.invalidate('Graph stale; retaining last cooperative positions')
    def display(self):
        self.expire()
        if not self.last_display:return self
        self.last_display.held=self.profile is not self.last_display.profile
        self.last_display.age=max(0.,self.clock()-self.last_display.stamp)
        return self.last_display
