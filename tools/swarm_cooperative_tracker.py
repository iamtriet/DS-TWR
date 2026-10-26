"""Joint range-domain IEKF with centroid or measured collector-origin gauge.

The optional stationary collector fixes translation. Otherwise no board is fixed.
Global translation/heading have no external observations. BNO085 supplies
motion-dependent process noise, not an invented absolute acceleration axis.
"""
import numpy as np
from itertools import combinations
from swarm_range_window import range_window


class MovingFrameAligner:
    """Rigid display transform only: never changes inter-node distances."""
    def __init__(self):
        self.previous={};self.rotation=np.eye(2);self.translation=np.zeros(2)

    def align(self,ids,points,quiet,origin=0):
        if origin:
            # Translation is physically measured through the collector ranges.
            # Only yaw remains a gauge; never translate away from this origin.
            points=points-points[ids.index(origin)]
            common=[i for i,node in enumerate(ids) if node!=origin and node in quiet and node in self.previous]
            used=[]
            if len(common)>=2:
                a=points[common];b=np.array([self.previous[ids[i]] for i in common])
                if np.linalg.matrix_rank(a,tol=.03)==2:
                    u,_,vt=np.linalg.svd(a.T@b)
                    if np.linalg.det(u@vt)<0:u[:,-1]*=-1
                    self.rotation=u@vt;used=[ids[i] for i in common]
            shown=points@self.rotation
            self.previous.update({node:shown[i].copy() for i,node in enumerate(ids)})
            return shown,used
        common=[i for i,node in enumerate(ids) if node in quiet and node in self.previous]
        used=[]
        if len(common)>=3:
            a=points[common];b=np.array([self.previous[ids[i]] for i in common])
            ac=a-a.mean(0);bc=b-b.mean(0)
            singular=np.linalg.svd(ac,compute_uv=False)
            if singular[-1]>.03 and singular[-1]>.05*singular[0]:
                u,_,vt=np.linalg.svd(ac.T@bc)
                if np.linalg.det(u@vt)<0:u[:,-1]*=-1
                self.rotation=u@vt;self.translation=b.mean(0)-a.mean(0)@self.rotation
                used=[ids[i] for i in common]
        shown=points@self.rotation+self.translation
        self.previous.update({node:shown[i].copy() for i,node in enumerate(ids)})
        return shown,used


class CooperativeRangeTracker:
    def __init__(self,origin=0):
        self.origin=origin
        self.ids=[];self.x=None;self.p=None;self.epoch=None;self.used={}
        self.frame=MovingFrameAligner()
        self.warmup_until=None
        self.rest_members=set();self.rest_since={};self.last_bno={}
        self.range_motion_since={}
        self.last_motion=-np.inf

    def solve(self,ids,d,edges,nodes,history,now,use_imu=True):
        from swarm_localization import WeightedSMACOFSolver
        from swarm_core import weighted_kabsch
        n=len(ids);index={node:i for i,node in enumerate(ids)}
        if self.origin and self.origin not in ids:raise ValueError('Origin collector is missing; cannot publish sniffer-relative coordinates')
        if n<3:raise ValueError('Need at least three connected mobile nodes')
        if len(edges)<2*n-3:raise ValueError('Insufficient ranges to constrain the moving swarm')
        epoch=max(r.effective for r in edges)
        # Fit each link once per solve, not once per endpoint and decision.
        trends={}
        for r in edges:
            key=tuple(sorted((r.a,r.b)));rows=history.get(key,())
            trends[key]=range_window(rows,rows[-1][0],.6) if rows else None
        if ids!=self.ids or self.warmup_until is None:self.warmup_until=epoch+1.
        quality={};quiet=set();process=[];candidates=set();stable=set();departing=set()
        for node in ids:
            imu=nodes[node].imu
            fresh=bool(use_imu and imu and 0<=now-imu['received']<.25 and imu.get('sample_age_ms',1000)<=100)
            bno=fresh and imu.get('sensor')=='BNO085'
            if bno and imu['moving']:self.last_motion=now
            if bno:self.last_bno[node]=now
            if bno and (imu['moving'] or imu['quiet_ms']<500):departing.add(node)
            if now-self.last_bno.get(node,-np.inf)>.35:departing.add(node)
            quiet_links=0
            if bno and not imu['moving'] and imu['quiet_ms']>=2000:
                candidates.add(node)
                for other in ids:
                    if other==node:continue
                    v=trends.get(tuple(sorted((node,other))))
                    if v and v['samples']>=12 and now-v['newest']<.25 and abs(v['radial_speed'])<.06 and v['sigma']<.04:
                        quiet_links+=1;stable.add(tuple(sorted((node,other))))
            magnitude=imu.get('score_mg',0)*.00980665 if bno else None
            process.append(max(2.5 if imu['moving'] else .6,min(4.,.6+3*magnitude)) if bno else 2.5)
            quality[node]={'imu_sensor':imu.get('sensor') if fresh else None,'bno_fresh':bno,
                           'relative_rest':node in quiet,'quiet_range_links':quiet_links,
                           'linear_acceleration_norm_mps2':magnitude}
        # A moving peer must not disqualify the stable subgroup. Require a
        # mutually consistent triangle, then extend it with quiet peers.
        for triple in combinations(sorted(candidates),3):
            if not all(pair in stable for pair in combinations(triple,2)):continue
            group=set(triple)
            for node in sorted(candidates-group):
                # A single noisy/NLOS edge must not prevent a quiet node from
                # joining. Two stable links plus fresh BNO quiet evidence are
                # required; coherent range motion can still release it below.
                if sum(tuple(sorted((node,other))) in stable for other in group)>=2:group.add(node)
            if len(group)>len(quiet):quiet=group
        # A noisy edge must not rebuild the coordinate frame on every sample.
        # IMU motion releases immediately; coherent relative range velocity
        # also releases constant-speed motion that an accelerometer cannot see.
        checking=self.rest_members.intersection(ids)-departing
        for node in checking:
            changing=0;still_directions=[]
            for other in checking-{node}:
                v=trends.get(tuple(sorted((node,other))))
                if v and v['samples']>=12 and now-v['newest']<.25 and v['sigma']<.04 and abs(v['radial_speed'])>.06:changing+=1
                if (v and v['samples']>=12 and now-v['newest']<.25 and v['sigma']<.04
                        and abs(v['radial_speed'])<.03 and self.x is not None and ids==self.ids):
                    delta=self.x.reshape(-1,4)[index[node],:2]-self.x.reshape(-1,4)[index[other],:2]
                    if np.linalg.norm(delta)>.05:still_directions.append(delta/np.linalg.norm(delta))
            # Two nonparallel stable ranges contradict motion inferred only
            # from other wandering links. Fresh IMU motion already removed the
            # node above, so this veto never overrides measured IMU motion.
            stable_geometry=len(still_directions)>=2 and np.linalg.svd(np.array(still_directions),compute_uv=False)[-1]>.25
            if changing>=2 and not stable_geometry and now-self.last_motion>=2.:
                self.range_motion_since.setdefault(node,now)
                if now-self.range_motion_since[node]>=.25:departing.add(node)
            else:self.range_motion_since.pop(node,None)
        for node in departing:self.range_motion_since.pop(node,None)
        self.rest_members.intersection_update(ids)
        self.rest_members-=departing
        for node in ids:
            if node in quiet and node not in departing:
                self.rest_since.setdefault(node,now)
                if now-self.rest_since[node]>=.2:self.rest_members.add(node)
            else:self.rest_since.pop(node,None)
        quiet=set(self.rest_members) if len(self.rest_members)>=3 else set()
        if self.origin:quiet.add(self.origin)
        for node in ids:quality[node]['relative_rest']=node in quiet
        if ids!=self.ids or self.x is None:
            w=np.zeros_like(d)
            for r in edges:
                i,j=index[r.a],index[r.b];w[i,j]=w[j,i]=max(.02,r.quality if r.quality is not None else .25)
            points=WeightedSMACOFSolver().solve(d,w)
            # Preserve the existing relative frame when membership changes.
            common=[node for node in ids if node in self.ids]
            if self.x is not None and len(common)>=3:
                a=points[[index[k] for k in common]];b=self.x.reshape(-1,4)[[self.ids.index(k) for k in common],:2]
                aligned=weighted_kabsch(a,b,np.ones(len(a)))
                u,_,vt=np.linalg.svd((a-a.mean(0)).T@(aligned-aligned.mean(0)))
                if np.linalg.det(u@vt)<0:u[:,-1]*=-1
                points=(points-a.mean(0))@(u@vt)+aligned.mean(0)
            points-=points[index[self.origin]] if self.origin else points.mean(0)
            rigidity=np.zeros((len(edges),2*n))
            for row,r in enumerate(edges):
                i,j=index[r.a],index[r.b];delta=points[i]-points[j]
                unit=delta/max(np.linalg.norm(delta),1e-9)
                rigidity[row,2*i:2*i+2]=unit;rigidity[row,2*j:2*j+2]=-unit
            if np.linalg.matrix_rank(rigidity,tol=1e-4)<2*n-3:
                raise ValueError('Moving swarm geometry is not locally rigid')
            self.x=np.c_[points,np.zeros((n,2))].ravel();self.p=np.diag(np.tile([.08**2,.08**2,.4**2,.4**2],n))
            if self.origin:
                sl=slice(4*index[self.origin],4*index[self.origin]+4)
                self.p[sl,:]=0.;self.p[:,sl]=0.
            self.ids=list(ids);self.epoch=epoch;self.used={}
        dt=max(0.,epoch-self.epoch);step=min(.1,dt)
        old=self.x.reshape(n,4).copy();prior=self.x.copy();cov=self.p.copy()
        f=np.eye(4*n);q=np.zeros_like(cov)
        for i,node in enumerate(ids):
            sl=slice(4*i,4*i+4)
            if dt>.3:prior[4*i+2:4*i+4]=0.;cov[sl,sl]+=np.diag([.1**2]*2+[.4**2]*2)
            if node in quiet:
                prior[4*i+2:4*i+4]=0.
                # The state reset must reset its covariance too. Previously
                # unobserved velocity uncertainty grew despite velocity=0,
                # then leaked into position and defeated the range weights.
                vs=slice(4*i+2,4*i+4)
                cov[vs,:]=0.;cov[:,vs]=0.;cov[vs,vs]=np.eye(2)*.02**2
            f[4*i:4*i+2,4*i+2:4*i+4]=np.eye(2)*step
            g=np.vstack((np.eye(2)*step**2/2,np.eye(2)*step))
            q[sl,sl]=g@g.T*(.25 if node in quiet else process[i])**2
            if node==self.origin:q[sl,sl]=0.;cov[sl,:]=0.;cov[:,sl]=0.
        prior=f@prior;cov=f@cov@f.T+q
        fresh=[r for r in edges if r.effective>self.used.get(tuple(sorted((r.a,r.b))),-np.inf)]
        estimate=prior.copy();weights=np.ones(len(fresh))
        if fresh:
            z=np.array([r.distance for r in fresh]);noise=np.array([.05/np.sqrt(max(.03,r.quality if r.quality is not None else .25)) for r in fresh])
            # RF first-path quality alone missed strongly fluctuating links on
            # the real stationary fleet. Estimate additional noise from a short
            # detrended range window: actual radial motion is not noise.
            for row,r in enumerate(fresh):
                window=trends.get(tuple(sorted((r.a,r.b))))
                if window:noise[row]=max(noise[row],2.*window['sigma'])
            for _ in range(4):
                h=np.zeros((len(fresh),4*n));pred=[]
                for row,r in enumerate(fresh):
                    i,j=index[r.a],index[r.b];offset=r.effective-epoch
                    a=estimate[4*i:4*i+2]+offset*estimate[4*i+2:4*i+4]
                    b=estimate[4*j:4*j+2]+offset*estimate[4*j+2:4*j+4]
                    delta=a-b;length=max(np.linalg.norm(delta),1e-6);unit=delta/length
                    h[row,4*i:4*i+4]=np.r_[unit,offset*unit];h[row,4*j:4*j+4]=-np.r_[unit,offset*unit];pred.append(length)
                residual=z-pred
                scale=np.sqrt(noise**2+np.einsum('ij,jk,ik->i',h,cov,h))
                weights=np.minimum(1.,2.5*scale/np.maximum(np.abs(residual),1e-9));rr=np.diag(noise**2/weights**2)
                k=np.linalg.solve(h@cov@h.T+rr,h@cov).T
                # Schmidt/consider-state update: a corroborated quiet subgroup
                # retains its shape instead of absorbing a moving peer's range
                # errors. Its covariance still enters S; Joseph update below
                # preserves uncertainty and cross-correlations. No permanent
                # anchor IDs; release immediately when motion evidence changes.
                for node in quiet if epoch>=self.warmup_until else ():
                    i=index[node]
                    # Quiet-to-quiet ranges still refine an imperfect initial
                    # shape. Only moving-peer errors are prevented from dragging
                    # quiet states. Never freeze a bad startup geometry forever.
                    for row,r in enumerate(fresh):
                        if now-self.last_motion<2. or r.a not in quiet or r.b not in quiet:k[4*i:4*i+4,row]=0.
                if self.origin:
                    i=index[self.origin];k[4*i:4*i+4,:]=0.
                estimate=prior+k@(residual+h@(estimate-prior))
            a=np.eye(4*n)-k@h;cov=a@cov@a.T+k@rr@k.T
            for r in fresh:self.used[tuple(sorted((r.a,r.b)))]=r.effective
        values=estimate.reshape(n,4)
        for i,node in enumerate(ids):
            if node in quiet:values[i,2:]=0.
            speed=np.linalg.norm(values[i,2:])
            if speed>1.:values[i,2:]/=speed
        # Gauge: remove common translation and common velocity; no node pinned.
        if self.origin:values[index[self.origin]]=0.
        else:
            values[:,:2]-=values[:,:2].mean(0);values[:,2:]-=values[:,2:].mean(0)
        # Bound the whole correction with one scale so recentering stays exact.
        delta=values[:,:2]-old[:,:2];largest=np.max(np.linalg.norm(delta,axis=1))
        if step>0 and largest>step:
            remaining=delta*(1-step/largest);values[:,:2]=old[:,:2]+delta*step/largest
            for i in range(n):cov[4*i:4*i+2,4*i:4*i+2]+=np.outer(remaining[i],remaining[i])
        # Covariance follows the same centroid/mean-velocity conditioning.
        # This is a display gauge, not an external position observation.
        gauge=np.eye(4*n)
        if not self.origin:
            for component in range(4):
                indices=np.arange(component,4*n,4)
                gauge[np.ix_(indices,indices)]-=1./n
        cov=gauge@cov@gauge.T
        self.x=values.ravel();self.p=(cov+cov.T)/2;self.epoch=epoch
        positions=values[:,:2];errors=[np.linalg.norm(positions[index[r.a]]-positions[index[r.b]])-r.distance for r in edges]
        sigma={node:float(np.sqrt(max(0.,np.linalg.eigvalsh(cov[4*i:4*i+2,4*i:4*i+2])[-1]))) for i,node in enumerate(ids)}
        for node in ids:quality[node]['filter_position_sigma_m']=sigma[node]
        shown,frame_nodes=self.frame.align(ids,positions,quiet,self.origin)
        return np.c_[shown,np.zeros(n)],dict(rms=float(np.sqrt(np.mean(np.square(errors)))),
            uncertainty=max(sigma.values()),condition=None,converged=True,range_domain=True,
            stationary=sorted(quiet),unavailable_nodes={},node_sigma=sigma,node_quality=quality,
            cooperative=True,range_updates=len(fresh),range_downweighted=int(sum(weights<1.)),
            origin_node=self.origin,frame_nodes=frame_nodes,frame_mode=('sniffer-origin' if self.origin else 'quiet-subgroup') if frame_nodes else ('sniffer-origin-relative-heading' if self.origin else 'relative-fallback'),
            observability='adaptive relative frame; absolute translation and heading unobserved')
