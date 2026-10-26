"""Strict post-localization rejection. Never modifies the UWB solver or source."""
from copy import deepcopy
import math
from collections import deque


class PositionGate:
    def __init__(self, max_speed=.6):
        if not math.isfinite(max_speed) or max_speed <= 0:
            raise ValueError('max_speed must be positive and finite')
        self.max_speed=max_speed;self.frame=None
        self.quiet_since={};self.stationary={};self.candidates={};self.last={};self.seen={};self.rejected={};self.stamp=None;self.cached=None

    def candidate_ready(self,n,stamp,p,sigma,supported):
        if not supported or sigma is None or sigma>.08:
            self.candidates.pop(n,None);return False
        rows=self.candidates.setdefault(n,deque())
        if rows and (stamp<=rows[-1][0] or stamp-rows[-1][0]>.25):rows.clear()
        rows.append((stamp,list(p)))
        while rows and stamp-rows[0][0]>2.:rows.popleft()
        if len(rows)<10 or stamp-rows[0][0]<1.5:return False
        # A compact stationary cluster OR a physically coherent slow trajectory.
        # This is a new location acquisition, never a measured travel segment.
        stable=max(math.dist(q,rows[0][1]) for _,q in rows)<=.08
        coherent=all(math.dist(a[1],b[1])<=self.max_speed*(b[0]-a[0])+1e-9 for a,b in zip(rows,list(rows)[1:]))
        return stable or coherent

    def update(self, source, generation, now):
        if not source:return {}
        # A master election does not grant permission to teleport.
        frame=(generation,source.get('metrics',{}).get('origin_node'))
        if frame!=self.frame:
            self.__init__(self.max_speed);self.frame=frame
        stamp=source.get('time',0)
        if stamp==self.stamp and self.cached is not None:
            return deepcopy(self.cached)
        out=deepcopy(source);self.stamp=stamp;self.rejected={}
        nodes={int(n['id']):n for n in source.get('nodes',[])}
        held=[];reacquired=[];positions={};states={int(k):v for k,v in source.get('node_states',{}).items()}
        quality=source.get('metrics',{}).get('node_quality',{})
        for key,p in source.get('positions',{}).items():
            n=int(key);previous=self.last.get(n)
            previous_time=self.seen.get(n);self.seen[n]=stamp
            reason=None
            if previous_time is not None and stamp-previous_time>.3:
                self.stationary.pop(n,None);self.quiet_since.pop(n,None)
            imu=nodes.get(n,{}).get('imu') or {}
            quiet=imu.get('moving') is False and 0<=now-imu.get('received',0)<.3
            if not quiet:
                self.quiet_since.pop(n,None);self.stationary.pop(n,None)
            else:self.quiet_since.setdefault(n,stamp)
            q=quality.get(str(n),quality.get(n,{}));sigma=q.get('filter_position_sigma_m',source.get('uncertainty'))
            if len(p)<2 or not all(math.isfinite(v) for v in p):
                reason='NONFINITE POSITION'
            elif n!=frame[1]:
                if states.get(n,source.get('state')) not in ('measured-supported','estimated'):
                    reason='POSITION NOT SUPPORTED'
                elif sigma is None or not math.isfinite(sigma) or sigma<0 or sigma>.15 or now-source.get('published_at',stamp)>.5:
                    reason='POSITION UNCERTAIN'
                elif previous is not None:
                    if quiet and n not in self.stationary and stamp-self.quiet_since[n]>=.4 and stamp-previous['t']<=.25:
                        self.stationary[n]=list(previous['xy'])
                    if n in self.stationary:
                        p=list(self.stationary[n]);held.append(n)
                    # Use the latest observed interval, NOT time since acceptance.
                    # Rejections/outages never accumulate a large travel allowance.
                    dt=min(.2,max(0.,stamp-(previous_time if previous_time is not None else previous['t'])))
                    distance=math.dist(p,previous['xy'])
                    if distance>self.max_speed*dt+1e-9:
                        reason=f'SPEED LIMIT {self.max_speed:.2f} m/s'
            if reason and reason.startswith('SPEED LIMIT'):
                supported=(states.get(n)=='measured-supported' and not source.get('metrics',{}).get('geometry_inconsistent'))
                if self.candidate_ready(n,stamp,p,sigma,supported):
                    reason=None;reacquired.append(n);states[n]='reacquired'
            elif reason:
                self.candidates.pop(n,None);self.stationary.pop(n,None);self.quiet_since.pop(n,None)
            if reason:
                self.rejected[n]=reason;states[n]='outlier-rejected'
                if previous is not None:positions[n]=list(previous['xy'])
                continue
            self.candidates.pop(n,None)
            positions[n]=list(p);self.last[n]=dict(t=stamp,xy=list(p))
        for n,previous in self.last.items():
            if n not in positions and n not in {int(k) for k in source.get('positions',{})}:
                positions[n]=list(previous['xy']);states[n]='outlier-rejected'
                self.rejected[n]='SIGNAL LOST / LAST ACCEPTED';self.candidates.pop(n,None)
                self.stationary.pop(n,None);self.quiet_since.pop(n,None)
        out['stationary_held']=held
        for n in held:out.setdefault('velocity',{})[n]=[0.,0.,0.]
        out['position_reacquired']=reacquired
        out['positions']=positions;out['node_states']=states
        out['position_rejections']={str(n):r for n,r in self.rejected.items()}
        out['position_filter']=dict(max_speed_mps=self.max_speed,held_nodes=sorted(self.rejected))
        self.cached=out
        return deepcopy(out)
