"""Conditional stationary reference: isolate a moving node from the gauge.

Range stability cannot detect common rigid motion. References are relative,
released when their internal distances change, and never fabricate ranges.
"""
from collections import deque
from itertools import combinations
import numpy as np
from scipy.optimize import least_squares


class StationaryReference:
    def __init__(self):
        self.history=deque();self.ids=None;self.reference=None;self.core=();self.unavailable={}

    def update(self,ids,d,w,x,now):
        if self.ids!=list(ids):
            self.__init__();self.ids=list(ids)
        self.unavailable={}
        self.history.append((now,d.copy()))
        while self.history and now-self.history[0][0]>.8:self.history.popleft()
        if len(ids)<4 or len(self.history)<8 or now-self.history[0][0]<.5:
            return x,set()
        stack=np.stack([v for _,v in self.history])
        # Explicit finite counts avoid treating missing links as stationary.
        valid=np.isfinite(stack)
        count=valid.sum(0)
        mean=np.divide(np.where(valid,stack,0).sum(0),count,out=np.zeros_like(d),where=count>0)
        variance=np.divide(np.where(valid,(stack-mean)**2,0).sum(0),count,out=np.full_like(d,np.inf),where=count>0)
        stable=(count>=8)&(variance<.035**2)&(w>.015)
        if self.reference is None:
            # One noisy/mobile node must not prevent the other three from
            # establishing a reference. Bootstrap from their distances alone.
            cores=[c for c in combinations(range(len(ids)),3)
                   if all(stable[i,j] for i,j in combinations(c,2))]
            if not cores:return x,set()
            core=min(cores,key=lambda c:sum(variance[i,j] for i,j in combinations(c,2)))
            a,b,c=core;ab=mean[a,b];ac=mean[a,c];bc=mean[b,c]
            if ab<.08:return x,set()
            along=(ab*ab+ac*ac-bc*bc)/(2*ab);height2=ac*ac-along*along
            if height2<.04**2:return x,set()
            cross=np.linalg.det(np.stack((x[b]-x[a],x[c]-x[a])))
            triangle=np.array([[0.,0.],[ab,0.],[along,np.copysign(np.sqrt(height2),cross)]])
            from swarm_core import weighted_kabsch
            self.reference=x.copy()
            self.reference[list(core)]=weighted_kabsch(triangle,x[list(core)],np.ones(3))
        ref=self.reference
        expected=np.linalg.norm(ref[:,None]-ref[None,:],axis=2)
        stable &= np.abs(d-expected)<.08
        candidates=[]
        for core in combinations(range(len(ids)),3):
            if not all(stable[i,j] for i,j in combinations(core,2)):continue
            shape=np.linalg.svd(ref[list(core)]-ref[list(core)].mean(0),compute_uv=False)
            if shape[-1]<.04:continue
            score=sum(abs(d[i,j]-expected[i,j]) for i,j in combinations(core,2))
            candidates.append((score-(.03 if core==self.core else 0),core))
        if not candidates:
            self.reference=None;self.core=();self.history.clear()
            return x,set()
        core=min(candidates)[1];result=ref.copy();stationary=set(core)
        for i in range(len(ids)):
            if i in core:continue
            links=[j for j in core if np.isfinite(d[i,j]) and w[i,j]>.05]
            if len(links)<3:
                self.unavailable[ids[i]]='Fewer than three fresh reference links'
                result[i]=x[i];continue
            anchors=ref[links];distances=d[i,links];weights=np.sqrt(w[i,links])
            fit=least_squares(lambda p:weights*(np.linalg.norm(p-anchors,axis=1)-distances),
                              x[i],loss='soft_l1',f_scale=.04,max_nfev=30)
            if not fit.success or np.sqrt(np.mean((np.linalg.norm(fit.x-anchors,axis=1)-distances)**2))>.15:
                self.unavailable[ids[i]]='Ranges inconsistent with stationary reference'
                result[i]=x[i];continue
            result[i]=fit.x
        self.core=core
        return result,{ids[i] for i in stationary}
