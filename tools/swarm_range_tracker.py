"""Timestamp-aware robust range-domain iterated EKF, one state per mobile.

State [x,y,vx,vy] is evaluated at the newest range epoch. Older observations
in the batch use p + (t_observation-t_epoch)*v. Each link timestamp is consumed
once. Covariance is conditional on the surveyed reference coordinates.
"""
import numpy as np


class RangeDomainTracker:
    def __init__(self):
        self.states={};self.used={}

    def update(self,node,anchors,records,seed,stationary=False):
        epoch=max(r.effective for r in records)
        old=self.states.get(node)
        if old is None:
            x=np.r_[seed,0.,0.];p=np.diag([.08**2]*2+[.4**2]*2)
            self.states[node]=(x,p,epoch)
            self.used[node]={tuple(sorted((r.a,r.b))):r.effective for r in records}
            return x[:2].copy(),.08,{'range_updates':0,'range_initialized':True}
        x,p,last=old;dt=epoch-last
        used=self.used[node]
        fresh=[(a,r) for a,r in zip(anchors,records)
               if r.effective>used.get(tuple(sorted((r.a,r.b))),-np.inf)]
        if dt<=0 or not fresh:
            return x[:2].copy(),float(np.sqrt(np.linalg.eigvalsh(p[:2,:2])[-1])),{'range_updates':0}
        prior=x.copy();cov=p.copy();step=min(dt,.1)
        if dt>.3:prior[2:]=0.;cov+=np.diag([.1**2]*2+[.4**2]*2)
        if stationary:
            prior[2:]=0.;cov[:2,2:]=0.;cov[2:,:2]=0.
        f=np.eye(4);f[:2,2:]=np.eye(2)*step
        g=np.vstack((np.eye(2)*step**2/2,np.eye(2)*step))
        prior=f@prior;cov=f@cov@f.T+g@g.T*(.25 if stationary else 2.5)**2
        aa=np.array([a for a,r in fresh]);offset=np.array([r.effective-epoch for a,r in fresh])
        z=np.array([r.distance for a,r in fresh])
        sigma=np.array([.05/np.sqrt(max(.03,r.quality if r.quality is not None else .25)) for a,r in fresh])
        estimate=prior.copy()
        for _ in range(4):
            delta=estimate[:2]+offset[:,None]*estimate[2:]-aa
            length=np.maximum(np.linalg.norm(delta,axis=1),1e-6)
            unit=delta/length[:,None];h=np.c_[unit,offset[:,None]*unit]
            residual=z-length
            # Huber influence on raw range innovations; inflate R instead of
            # projecting an unreliable trilateration point into the filter.
            innovation_scale=np.sqrt(sigma**2+np.einsum('ij,jk,ik->i',h,cov,h))
            weight=np.minimum(1.,2.5*innovation_scale/np.maximum(np.abs(residual),1e-9))
            rr=np.diag(sigma**2/weight**2)
            k=np.linalg.solve(h@cov@h.T+rr,h@cov).T
            estimate=prior+k@(residual+h@(estimate-prior))
        a=np.eye(4)-k@h;cov=a@cov@a.T+k@rr@k.T
        if stationary:
            estimate[2:]=0.;cov[:2,2:]=0.;cov[2:,:2]=0.
        speed=np.linalg.norm(estimate[2:])
        if speed>1.:estimate[2:]/=speed
        change=estimate[:2]-x[:2];distance=np.linalg.norm(change)
        if distance>step:
            remaining=change*(1-step/distance)
            estimate[:2]=x[:2]+change*step/distance;cov[:2,:2]+=np.outer(remaining,remaining)
        for _,r in fresh:used[tuple(sorted((r.a,r.b)))]=r.effective
        self.states[node]=(estimate,cov,epoch)
        return estimate[:2].copy(),float(np.sqrt(max(0.,np.linalg.eigvalsh(cov[:2,:2])[-1]))),{
            'range_updates':len(fresh),'range_epoch':epoch,'range_downweighted':int(sum(weight<1.)),
            'range_residual_rms_m':float(np.sqrt(np.mean(residual**2)))}
