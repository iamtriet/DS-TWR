"""Local-metre SAR mission log. No actuator commands or victim detector."""
from dataclasses import dataclass,asdict
from datetime import datetime,timezone
import json,math,csv
from pathlib import Path
from cockroach_mapping import BehaviorMap
from automatic_fences import AutomaticFences
from trajectory_guard import TrajectoryGuard

def distance(a,b):return math.hypot(a[0]-b[0],a[1]-b[1])

def segment_distance(p,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1];den=dx*dx+dy*dy
    t=max(0.,min(1.,((p[0]-a[0])*dx+(p[1]-a[1])*dy)/den)) if den else 0.
    return distance(p,(a[0]+t*dx,a[1]+t*dy))

def fence_state(p,polygon,margin):
    if len(polygon)<3:return 'NO FENCE'
    inside=False;nearest=float('inf')
    for a,b in zip(polygon,polygon[1:]+polygon[:1]):
        nearest=min(nearest,segment_distance(p,a,b))
        if (a[1]>p[1])!=(b[1]>p[1]) and p[0]<(b[0]-a[0])*(p[1]-a[1])/(b[1]-a[1])+a[0]:inside=not inside
    if nearest<=margin:return 'BOUNDARY / UNCERTAIN'
    return 'INSIDE' if inside else 'OUTSIDE'

@dataclass
class BioProfile:
    # Operator configuration, NOT validated species-specific physiological limits.
    max_speed:float=1.
    active_seconds:float=120.
    rest_seconds:float=30.
    def validate(self):
        if not all(math.isfinite(v) and v>0 for v in asdict(self).values()):raise ValueError('Profile values must be finite and positive')
        return self

class Mission:
    def __init__(self):
        self.created=datetime.now(timezone.utc).isoformat();self.running=False;self.archive_review=False;self.post_position_filter=None
        self.polygon=[];self.margin=.20;self.origin=0;self.generation=None;self.identities={}
        self.profiles={};self.tracks={};self.events=[];self.markers=[];self.status={}
        self.last_stamp=None;self.last_good={};self.segment={};self.activity={};self.rest={};self.last_event={};self.term=None
        self.note='Start mission: automatic fences grow from observed paths and repeated turnaround evidence.'
        self.auto_fence=True;self.automatic_fences=AutomaticFences()
        self.bio_stamp={};self.biological_enabled=False
        self.origin_motion_since=None;self.origin_motion_last=None;self.origin_motion_samples=0;self.origin_motion_hold=False
        self.behavior_map=BehaviorMap();self.trajectory_guard=TrajectoryGuard()
    def biological_state(self,node,profile,motion,stamp,now):
        if not self.biological_enabled:
            moving=motion.get('moving') if 0<=now-motion.get('received',0)<.3 else None
            return 'MOVING' if moving else 'QUIET' if moving is False else 'IMU UNKNOWN'
        dt=min(.2,max(0.,stamp-self.bio_stamp.get(node,stamp)));self.bio_stamp[node]=stamp
        moving=motion.get('moving') if now-motion.get('received',0)<.3 else None
        if moving:
            self.activity[node]=self.activity.get(node,0)+dt;self.rest[node]=0.
        elif moving is False:
            self.rest[node]=self.rest.get(node,0)+dt
            if self.rest[node]>=profile.rest_seconds:self.activity[node]=0.
        return 'REST ADVISED' if self.activity.get(node,0)>=profile.active_seconds else ('MOVING' if moving else 'QUIET' if moving is False else 'IMU UNKNOWN')
    def event(self,kind,node,stamp,detail):
        key=(kind,node)
        if self.last_event.get(key)==detail:return
        self.last_event[key]=detail;self.events.append(dict(kind=kind,node=node,time=stamp,detail=detail))
    def set_rectangle(self,x0,y0,x1,y1):
        if self.running:raise ValueError('Pause the mission before changing the fence')
        if not all(math.isfinite(v) for v in (x0,y0,x1,y1)) or abs(x1-x0)<.1 or abs(y1-y0)<.1:raise ValueError('Fence must be at least 0.1 m wide/high')
        self.auto_fence=False
        self.polygon=[[min(x0,x1),min(y0,y1)],[max(x0,x1),min(y0,y1)],[max(x0,x1),max(y0,y1)],[min(x0,x1),max(y0,y1)]]
    def start(self,s,generation,now):
        origin=s.get('metrics',{}).get('origin_node',0)
        if not origin or origin not in {int(k) for k in s.get('positions',{})}:raise ValueError('Start live tracking with Sniffer origin first')
        if not self.auto_fence and not self.polygon:raise ValueError('Define the search fence first')
        if self.generation is not None and generation!=self.generation:raise ValueError('Tracking session changed. Export and create a new mission.')
        if self.origin and origin!=self.origin:raise ValueError('Origin changed. Create a new mission.')
        if now-s.get('published_at',s.get('time',0))>.5:raise ValueError('Tracking data is stale')
        self.origin=origin;self.generation=generation;self.running=True;self.last_stamp=None
        self.term=s.get('network',{}).get('term')
        self.identities={str(n['id']):n.get('uid') for n in s.get('nodes',[])}
        self.note='Recording observed insect paths; heading relative to the current tracking frame.'
        self.event('mission',0,now,'started')
    def clear_trajectories(self,now):
        self.tracks.clear();self.markers.clear();self.last_good.clear();self.segment.clear();self.status.clear()
        self.last_stamp=None;self.trajectory_guard=TrajectoryGuard()
        for node in list(self.behavior_map.history):self.behavior_map.gap(node)
        self.event('trajectory_reset',0,now,'all paths and route markers cleared; map retained')
        self.note='Trajectories cleared; map retained. New accepted samples start new paths.'
    def pause(self,now,reason='paused by operator'):
        self.running=False;self.note=reason;self.event('mission',0,now,reason)
        self.last_good.clear();self.trajectory_guard=TrajectoryGuard()
        for node in list(self.behavior_map.history):self.behavior_map.gap(node)
    def update(self,s,generation,now):
        if not self.running:return
        if generation!=self.generation or s.get('metrics',{}).get('origin_node')!=self.origin:
            self.pause(now,'Tracking frame changed; start a new mission.');return
        if self.term is not None and s.get('network',{}).get('term')!=self.term:
            self.pause(now,'Radio epoch changed; verify coordinates and start a new mission.');self.generation=-1;return
        nodes={n['id']:n for n in s.get('nodes',[])}
        if any(str(n) in self.identities and nodes[n].get('uid')!=self.identities[str(n)] for n in nodes):
            self.pause(now,'Board identity changed; start a new mission.');self.generation=-1;return
        imu=(nodes.get(self.origin,{}).get('imu') or {})
        # A single IMU motion flag is not proof of a displaced coordinate origin.
        # Hold observations immediately, but require distinct fresh samples over
        # one second before escalating the warning. IMU alone cannot establish
        # displacement. The operator-configured stationary origin remains assumed.
        received=imu.get('received',0)
        if imu.get('moving') and 0<=now-received<.5:
            if self.origin_motion_last is None or received-self.origin_motion_last>.5:
                self.origin_motion_since=received;self.origin_motion_samples=0
            if self.origin_motion_last is None or received>self.origin_motion_last:
                self.origin_motion_samples+=1;self.origin_motion_last=received
            self.origin_motion_hold=True
            self.trajectory_guard=TrajectoryGuard()
            self.last_good.clear()
            for node in list(self.behavior_map.history):self.behavior_map.gap(node)
            for status in self.status.values():status.update(fence='UNKNOWN',guidance='WAIT: SNIFFER MOTION CHECK')
            self.note='Recording held: checking sniffer motion; brief IMU pulses recover automatically.'
            if self.origin_motion_samples>=4 and received-self.origin_motion_since>=1.:
                self.note='Mapping held: sustained sniffer IMU signal; live nodes remain visible. Waiting for quiet IMU.'
            return
        if self.origin_motion_hold:
            # Do not resume on stale/unknown IMU: wait for a fresh quiet sample.
            if imu.get('moving') is not False or not 0<=now-received<.5:
                self.note='Recording held: waiting for fresh quiet sniffer IMU.';return
            self.event('origin_motion',self.origin,now,'sniffer IMU signal cleared; recording resumed with path gaps; stationary origin assumed')
            self.note='Recording resumed after sniffer IMU signal; origin assumed stationary.'
            self.last_stamp=None
        self.origin_motion_since=None;self.origin_motion_last=None;self.origin_motion_samples=0;self.origin_motion_hold=False
        stamp=s.get('time',0);age=s.get('age');fresh=now-s.get('published_at',stamp)<=.5 and age is not None and age<=.3
        self.behavior_map.advance(now);self.automatic_fences.update(self.behavior_map)
        if stamp==self.last_stamp and fresh:return
        self.last_stamp=stamp
        positions={int(k):v for k,v in s.get('positions',{}).items()};states={int(k):v for k,v in s.get('node_states',{}).items()}
        quality=s.get('metrics',{}).get('node_quality',{})
        for node in set(nodes)|set(self.status):
            if node==self.origin:continue
            profile=self.profiles.setdefault(node,BioProfile());p=positions.get(node)
            bio=self.biological_state(node,profile,nodes.get(node,{}).get('imu') or {},stamp,now)
            q=quality.get(node,quality.get(str(node),{}));sigma=q.get('filter_position_sigma_m',s.get('uncertainty'))
            valid=(fresh and p is not None and all(math.isfinite(v) for v in p[:2]) and states.get(node)=='measured-supported'
                   and not s.get('metrics',{}).get('geometry_inconsistent') and sigma is not None and math.isfinite(sigma) and sigma<=.3
                   and now-nodes.get(node,{}).get('last',0)<=.5)
            if not valid:
                self.trajectory_guard.gap(node)
                self.behavior_map.gap(node)
                self.status[node]=dict(fence='UNKNOWN',bio=bio,speed=None,activity_s=self.activity.get(node,0))
                self.last_good.pop(node,None);self.event('quality',node,stamp,'path gap; unreliable or stale position');continue
            previous=self.last_good.get(node);dt=stamp-previous['t'] if previous else 0.
            speed=distance(p,previous['xy'])/dt if previous and dt>0 else 0.
            motion=nodes.get(node,{}).get('imu') or {}
            moving=motion.get('moving') if 0<=now-motion.get('received',0)<.3 else None
            if previous and dt>.5:self.trajectory_guard.gap(node)
            motion_quality,minimum_speed=self.trajectory_guard.check(node,stamp,list(p[:2]),float(sigma),moving,profile.max_speed) if self.biological_enabled else ('ACCEPTED',speed)
            if motion_quality!='ACCEPTED':
                self.behavior_map.gap(node);self.last_good.pop(node,None)
                self.status[node]=dict(fence='UNKNOWN',bio=bio,speed=speed,behavior=motion_quality,guidance='MAPPING HELD / CHECK TRACKING',minimum_speed=minimum_speed)
                self.event('quality',node,stamp,motion_quality);continue
            if previous is None:self.segment[node]=self.segment.get(node,0)+1
            row=dict(t=stamp,xy=list(p[:2]),sigma=float(sigma),segment=self.segment[node])
            rows=self.tracks.setdefault(node,[])
            if not rows or rows[-1]['segment']!=row['segment'] or stamp-rows[-1]['t']>=.2 or distance(row['xy'],rows[-1]['xy'])>=.05:rows.append(row)
            self.last_good[node]=row
            motion=nodes.get(node,{}).get('imu') or {}
            moving=motion.get('moving') if now-motion.get('received',0)<.3 else None
            self.behavior_map.observe(node,row,moving,bio=='REST ADVISED')
            self.automatic_fences.update(self.behavior_map)
            fence=self.automatic_fences.state(p,max(self.margin,2*float(sigma))) if self.auto_fence else fence_state(p,self.polygon,max(self.margin,2*float(sigma)))
            guidance='RETURN INWARD (advisory)' if fence=='OUTSIDE' else 'CHECK BOUNDARY' if fence=='BOUNDARY / UNCERTAIN' else 'REST ADVISED' if bio=='REST ADVISED' else 'EXPLORE'
            if self.auto_fence and fence=='CAUTION / SUSPECTED OBSTACLE':guidance='AVOID SUSPECTED AREA (advisory)'
            behavior=self.behavior_map.states.get(node,'UNKNOWN')
            if guidance=='EXPLORE' and ('LOW PROGRESS' in behavior or behavior=='TURNAROUND EVIDENCE'):guidance='TRY ANOTHER APPROACH (advisory)'
            self.status[node]=dict(fence=fence,bio=bio,speed=speed,activity_s=self.activity.get(node,0),guidance=guidance)
            self.status[node]['behavior']=behavior
            self.status[node]['minimum_speed']=minimum_speed
            self.event('fence',node,stamp,fence);self.event('bio',node,stamp,bio)
    def mark(self,node,label,confirmed=False,now=None):
        if not self.running or node not in self.last_good:raise ValueError('A fresh reliable selected tag position is required')
        row=self.last_good[node];path=self.tracks[node]
        if now is not None and now-row['t']>.5:raise ValueError('Selected tag position is stale')
        if not path or path[-1]['t']!=row['t']:path.append(dict(row))
        segments={p['segment'] for p in path}
        complete=len(segments)==1 and distance(path[0]['xy'],[0,0])<=max(.25,2*path[0]['sigma'])
        marker=dict(id=len(self.markers)+1,node=node,label=label or 'Suspected victim',confirmed=bool(confirmed),source='operator',
                    position=row['xy'][:],time=row['t'],point_count=len(path),route_status='continuous observed approach from origin' if complete else 'partial trace / gaps or start away from origin',
                    observed_length_m=sum(distance(a['xy'],b['xy']) for a,b in zip(path,path[1:]) if a['segment']==b['segment']),
                    human_passability='not assessed',sigma=row['sigma'])
        self.markers.append(marker);return marker
    def document(self):
        return dict(schema='virtual-geofencing-v1',created_utc=self.created,coordinates=dict(units='metres',plane='XY assumed',origin_node=self.origin,heading='relative; no room heading',identities=self.identities),
                    post_position_filter=self.post_position_filter,biological_enabled=self.biological_enabled,auto_fence=self.auto_fence,automatic_fences=self.automatic_fences.document(),fence=self.polygon,margin_m=self.margin,profiles={n:asdict(p) for n,p in self.profiles.items()},tracks=self.tracks,markers=self.markers,events=self.events,behavior_map=self.behavior_map.document(),
                    limitations=['Insect traces are not verified human rescue routes','No automatic victim detection','Bio profiles are operator assumptions, not measured fatigue','No actuator commands'])
    def save(self,path):
        path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.document(),ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
    @classmethod
    def load(cls,path):
        d=json.loads(Path(path).read_text(encoding='utf-8'))
        if d.get('schema')!='virtual-geofencing-v1':raise ValueError('Unsupported mission format')
        m=cls();m.post_position_filter=d.get('post_position_filter');m.biological_enabled=d.get('biological_enabled',True);m.created=d['created_utc'];m.polygon=d['fence'];m.margin=d['margin_m'];m.origin=d['coordinates']['origin_node'];m.identities=d['coordinates']['identities']
        m.profiles={int(n):BioProfile(**p).validate() for n,p in d['profiles'].items()};m.tracks={int(n):rows for n,rows in d['tracks'].items()}
        m.events=d['events'];m.markers=d['markers'];m.generation=-1;m.archive_review=True;m.note='ARCHIVE REVIEW: historical relative coordinates; create a new mission to record.'
        m.behavior_map=BehaviorMap.load(d.get('behavior_map',{}))
        m.auto_fence=d.get('auto_fence',False);m.automatic_fences.update(m.behavior_map)
        for point in [*m.polygon,*[r['xy'] for rows in m.tracks.values() for r in rows],*[r['position'] for r in m.markers]]:
            if len(point)!=2 or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in point):raise ValueError('Invalid mission coordinates')
        return m
    def export(self,path):
        self.save(path)
        with Path(path).with_suffix('.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(['node','time_monotonic_s','segment','x_m','y_m','conditional_sigma_m'])
            for node,rows in self.tracks.items():
                for row in rows:writer.writerow([node,row['t'],row['segment'],*row['xy'],row['sigma']])

        with Path(path).with_suffix('.evidence.csv').open('w',newline='',encoding='utf-8') as f:
            fields=['cell_x','cell_y','state','score','episodes','nodes','age_s']
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
            for cell in self.behavior_map.cells():
                writer.writerow(dict(cell_x=cell['cell'][0],cell_y=cell['cell'][1],state=cell['state'],score=cell.get('score',''),episodes=cell.get('episodes',cell.get('visits',0)),nodes=';'.join(map(str,cell['nodes'])),age_s=cell.get('age_s',0)))
        with Path(path).with_suffix('.fences.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(['type','x1_m','y1_m','x2_m','y2_m'])
            if self.auto_fence:
                for kind,cells in [('observed_frontier',self.automatic_fences.explored),('inferred_obstruction',self.automatic_fences.exclusions)]:
                    for a,b in self.automatic_fences.edges(cells):writer.writerow([kind,*a,*b])
            else:
                for a,b in zip(self.polygon,self.polygon[1:]+self.polygon[:1]):writer.writerow(['manual',*a,*b])
