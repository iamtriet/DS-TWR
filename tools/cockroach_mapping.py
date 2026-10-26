"""Conservative behavioral evidence map; does not observe physical obstacles."""
import math
from collections import deque
from dataclasses import dataclass, asdict

@dataclass
class InferenceConfig:
    half_life_s: float = 300.
    activate_score: float = 2.4
    release_score: float = 1.2
    max_sigma_m: float = .10
    def validate(self):
        if not all(math.isfinite(v) and v>0 for v in asdict(self).values()):
            raise ValueError("Inference parameters must be finite and positive")
        if self.release_score >= self.activate_score:
            raise ValueError("Release score must be below activation score")
        if not .01 <= self.max_sigma_m <= .3:
            raise ValueError("Maximum sigma must be between 0.01 and 0.30 m")
        return self


def dist(a,b):return math.hypot(a[0]-b[0],a[1]-b[1])

class BehaviorMap:
    def __init__(self,cell=.25,config=None):
        self.cell=cell;self.passage={};self.hypotheses={};self.history={};self.last_turn={};self.last_cross={};self.previous={}
        self.events=[];self.states={};self.now=0.;self.zones={}
        self.config=(config or InferenceConfig()).validate()
    def key(self,p):return (math.floor(p[0]/self.cell),math.floor(p[1]/self.cell))
    def center(self,key):return [(key[0]+.5)*self.cell,(key[1]+.5)*self.cell]
    def gap(self,node):
        self.history.pop(node,None);self.previous.pop(node,None);self.states[node]='UNKNOWN / TRACKING GAP'
    def advance(self,t):
        if math.isfinite(t):self.now=max(self.now,t)
        for key,h in self.hypotheses.items():
            score=sum(v.get('weight',1.)*2**(-max(0.,self.now-v['t'])/self.config.half_life_s) for v in h['votes'])
            nodes={v['node'] for v in h['votes']}
            diverse=len(nodes)>=2 or (len(h['votes'])>=2 and h['votes'][-1]['t']-h['votes'][0]['t']>=20.)
            before=h.get('active',False)
            active=(not h['contradicted'] and score>=(self.config.release_score if before else self.config.activate_score)
                    and len(h['votes'])>=3 and diverse)
            h['score']=score;h['active']=active
            if active!=before:self.events.append(dict(kind='fence_activated' if active else 'fence_released',t=self.now,cell=list(key),score=score))
    def zone(self,node,p,t,state):
        key=(self.key(p),state)
        entry=self.zones.setdefault(key,dict(nodes=set(),last=t,visits=0))
        entry['nodes'].add(node);entry['last']=t;entry['visits']+=1
    def evidence_key(self,target):
        # Merge nearby target estimates across cell boundaries, without spreading
        # evidence over an entire unvisited region.
        candidates=[(dist(target,h['votes'][-1].get('target',self.center(k))),k)
                    for k,h in self.hypotheses.items() if h['votes'] and not h['contradicted']]
        if candidates:
            separation,key=min(candidates)
            if separation<=self.cell*.65:return key
        return self.key(target)
    def add_evidence(self,node,t,target,tip,sigma,kind,weight):
        key=self.evidence_key(target)
        if key in self.passage and t-self.passage[key]['last']<10.:return False
        if t-self.last_turn.get(node,-math.inf)<10.:return False
        h=self.hypotheses.setdefault(key,dict(votes=[],last=t,radius=self.cell,contradicted=False))
        h['votes'].append(dict(node=node,t=t,weight=weight/(1+(sigma/self.config.max_sigma_m)**2),sigma=sigma,tip=tip,target=target,kind=kind))
        h['last']=t;h['contradicted']=False;self.last_turn[node]=t
        self.events.append(dict(kind=kind,node=node,t=t,cell=list(key),target=target,sigma=sigma))
        self.advance(t);self.states[node]=kind.upper()+' EVIDENCE';return True
    def extended_evidence(self,node,rows,t,threshold):
        if len(rows)<12 or t-rows[0]['t']<2.5:return False
        points=[r['p'] for r in rows];sigma=max(r['sigma'] for r in rows)
        # Closed, consistent orbit around an unvisited center, not arbitrary turns.
        center=[(min(p[i] for p in points)+max(p[i] for p in points))/2 for i in (0,1)]
        radii=[dist(p,center) for p in points];radius=sum(radii)/len(radii)
        if radius>max(.12,4*sigma) and max(radii)-min(radii)<radius*.55 and dist(points[0],points[-1])<max(.10,3*sigma):
            angles=[math.atan2(p[1]-center[1],p[0]-center[0]) for p in points]
            changes=[math.atan2(math.sin(b-a),math.cos(b-a)) for a,b in zip(angles,angles[1:])]
            total=sum(changes);travel=sum(abs(a) for a in changes)
            if abs(total)>5.2 and abs(total)>travel*.85 and sum(r['moving'] for r in rows)/len(rows)>.8:
                if self.add_evidence(node,t,center,points[-1],sigma,'circling',.9):return True
        # Approach followed by >=2 s active confinement. Quiet waiting is excluded.
        recent=[r for r in rows if t-r['t']<=2.]
        if recent and t-recent[0]['t']>=1.8 and all(r['moving'] for r in recent):
            tip=recent[0]['p'];start=points[0];length=dist(start,tip)
            if length>=max(.30,threshold) and max(dist(r['p'],tip) for r in recent)<max(.06,2*sigma):
                target=[tip[i]+(tip[i]-start[i])/length*self.cell for i in (0,1)]
                if self.add_evidence(node,t,target,tip,sigma,'blocked_approach',.65):return True
        return False
    def observe(self,node,row,moving,rest_advised=False):
        t=row['t'];p=row['xy'];sigma=row['sigma'];previous=self.previous.get(node)
        self.advance(t)
        if previous and t<=previous['t']:return
        self.previous[node]=row
        if previous and (t<=previous['t'] or t-previous['t']>.5 or row['segment']!=previous['segment']):
            self.history.pop(node,None);previous=None
        if not math.isfinite(sigma) or sigma<0 or sigma>self.config.max_sigma_m or moving is None:
            self.history.pop(node,None);self.states[node]='INSUFFICIENT MAPPING QUALITY';return
        history=self.history.setdefault(node,deque())
        history.append(dict(t=t,p=p[:],sigma=sigma,moving=bool(moving)))
        while history and t-history[0]['t']>20.:history.popleft()
        span=dist(history[0]['p'],p)
        threshold=max(.15,4*max(r['sigma'] for r in history))
        # Mark only observed movement endpoints, not an assumed free corridor
        # between noisy samples. Counts are separated by 2 s per node/cell.
        if moving and len(history)>1 and span>=threshold:
            key=self.key(p)
            if t-self.last_cross.get((node,key),-math.inf)>=2.:
                entry=self.passage.setdefault(key,dict(visits=0,nodes=set(),last=t))
                entry['visits']+=1;entry['nodes'].add(node);entry['last']=t;self.last_cross[node,key]=t
                # A later traversal contradicts a blocked-cell hypothesis.
                h=self.hypotheses.get(key)
                if h and t>h['last']:
                    self.events.append(dict(kind='hypothesis_contradicted',node=node,t=t,cell=list(key),retired_votes=[dict(v) for v in h['votes']]))
                    h['contradicted']=True;h['votes']=[]
        if rest_advised:
            self.states[node]='REST ADVISED';return
        if not moving:
            if t-history[0]['t']>=3. and all(not r['moving'] for r in history) and max(dist(r['p'],p) for r in history)<threshold:
                self.zone(node,p,t,'STATIONARY ZONE')
            self.states[node]='STATIONARY';return
        self.states[node]='EXPLORING'
        if t-history[0]['t']>=2. and max(dist(r['p'],p) for r in history)<threshold:
            self.states[node]='ACTIVE / LOW PROGRESS (not an obstacle detection)'
            self.zone(node,p,t,'LOW PROGRESS ZONE')
        if len(history)<5 or t-self.last_turn.get(node,-math.inf)<10.:return
        rows=list(history)
        if self.extended_evidence(node,rows,t,threshold):
            history.clear();history.append(dict(t=t,p=p[:],sigma=sigma,moving=True));return
        headings=[math.atan2(b['p'][1]-a['p'][1],b['p'][0]-a['p'][0]) for a,b in zip(rows,rows[1:]) if dist(a['p'],b['p'])>.005]
        turns=[math.atan2(math.sin(b-a),math.cos(b-a)) for a,b in zip(headings,headings[1:])]
        if len(turns)>=8 and max(map(abs,turns))<.7 and abs(sum(turns))>1.:
            self.states[node]='CURVED PATH / WAITING FOR LOOP';return
        start=rows[0]['p']
        tip_index=max(range(len(rows)),key=lambda i:dist(start,rows[i]['p']))
        if tip_index==0 or tip_index==len(rows)-1:return
        tip=rows[tip_index]['p'];a=[tip[i]-start[i] for i in (0,1)];b=[p[i]-tip[i] for i in (0,1)]
        la,lb=math.hypot(*a),math.hypot(*b)
        if la<max(.30,threshold) or lb<threshold or sum(a[i]*b[i] for i in (0,1))/(la*lb)>-.5:return
        if t-rows[tip_index]['t']<.3:return
        # Hypothesis lies beyond the observed turn, never on a visited tip.
        radius=max(self.cell,2*max(r['sigma'] for r in rows))
        target=[tip[i]+a[i]/la*radius for i in (0,1)];key=self.evidence_key(target)
        self.last_turn[node]=t;history.clear();history.append(dict(t=t,p=p[:],sigma=sigma,moving=True))
        # Historical passage is not a permanent veto: environments can change.
        if key in self.passage and t-self.passage[key]['last']<10.:
            self.states[node]='TURNAROUND IN OBSERVED AREA';return
        h=self.hypotheses.setdefault(key,dict(votes=[],last=t,radius=radius,contradicted=False))
        h['votes'].append(dict(node=node,t=t,weight=1./(1.+(max(r['sigma'] for r in rows)/self.config.max_sigma_m)**2),sigma=max(r['sigma'] for r in rows),tip=tip,target=target));h['last']=t;h['radius']=max(h['radius'],radius);h['contradicted']=False
        self.advance(t)
        # Evidence score is deliberately not represented as a probability.
        self.states[node]='TURNAROUND EVIDENCE';self.events.append(dict(kind='turnaround',node=node,t=t,cell=list(key),tip=tip,target=target,weight=h['votes'][-1]['weight'],sigma=h['votes'][-1]['sigma']))
    def cells(self):
        self.advance(self.now)
        rows=[dict(cell=list(k),center=self.center(k),state='OBSERVED PASSAGE',visits=v['visits'],nodes=sorted(v['nodes']),last_seen=v['last'],age_s=max(0.,self.now-v['last'])) for k,v in self.passage.items()]
        for (k,state),v in self.zones.items():
            if self.now-v['last']<=30.:rows.append(dict(cell=list(k),center=self.center(k),state=state,nodes=sorted(v['nodes']),visits=v['visits'],last_seen=v['last'],age_s=max(0.,self.now-v['last'])))
        for k,h in self.hypotheses.items():
            if h['contradicted'] or not h['votes']:continue
            nodes={v['node'] for v in h['votes']};count=len(h['votes'])
            state='SUSPECTED OBSTRUCTION' if h.get('active') else 'TURNAROUND EVIDENCE'
            rows.append(dict(cell=list(k),center=self.center(k),state=state,episodes=count,nodes=sorted(nodes),radius=h['radius'],evidence_types=sorted({v.get('kind','turnaround') for v in h['votes']}),score=h.get('score',0.),last_seen=h['last'],age_s=max(0.,self.now-h['last']),evidence_level='REPEATED' if h.get('active') else 'WEAK / AGING'))
        return rows
    def document(self):
        return dict(algorithm='behavior-evidence-v5',config=asdict(self.config),evaluated_at=self.now,cell_size_m=self.cell,cells=self.cells(),events=self.events,
                    hypotheses=[dict(cell=list(k),**v) for k,v in self.hypotheses.items()],
                    semantics='Heuristic behavioral evidence, not a physical occupancy sensor; unvisited space is unknown')
    @classmethod
    def load(cls,d):
        m=cls(d.get('cell_size_m',.25),InferenceConfig(**d.get('config',{})))
        m.now=d.get('evaluated_at',0.)
        if not math.isfinite(m.cell) or not .05<=m.cell<=5:raise ValueError('Invalid map resolution')
        for r in d.get('cells',[]):
            k=tuple(r['cell'])
            if len(k)!=2 or not all(isinstance(v,int) for v in k):raise ValueError('Invalid map cell')
            if r['state'] in ('QUIET / REST ZONE','STATIONARY ZONE','LOW PROGRESS ZONE'):m.zones[(k,r['state'])]=dict(visits=r['visits'],nodes=set(r['nodes']),last=r.get('last_seen',0.))
            if r['state']=='OBSERVED PASSAGE':m.passage[k]=dict(visits=r['visits'],nodes=set(r['nodes']),last=r.get('last_seen',0.))
        m.hypotheses={tuple(r['cell']):{k:v for k,v in r.items() if k!='cell'} for r in d.get('hypotheses',[])}
        m.events=d.get('events',[]);return m
