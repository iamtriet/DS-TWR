"""Executable 30-node cooperative localization pipeline.

Run: python tools/swarm_localization.py --seconds 30
Or:  python tools/swarm_localization.py --headless --seconds 10 --output build/swarm-simulation
Simulation feeds actual clock-domain broadcast records through DS-TWR assembly;
truth is used only for visualization/error scoring, never by the estimator.
"""
import argparse,json,math,time
from pathlib import Path
from collections import deque
import numpy as np
from scipy.sparse.csgraph import shortest_path,connected_components
from threadpoolctl import threadpool_limits
from swarm_core import Beacon,SwarmLedger,weighted_kabsch
from uwb3d_protocol import MASK40,DTU,C

class AsymmetricDSTWRAssembler(SwarmLedger):
    """Build symmetric D/W with explicit missing-data zeros in W.

    SwarmLedger follows RX sequence references A0 -> B0 -> A1 -> B1.
    Ra=(TX_A1-TX_A0)-reply_A1[B], Da=reply_B0[A],
    Rb=(TX_B1-TX_B0)-reply_B1[A], Db=reply_A1[B].
    ToF=(Ra*Rb-Da*Db)/(Ra+Rb+Da+Db), using integer products before division.
    Future broadcasts confirm A0, B0 and A1; B1 TX cancels algebraically.
    """
    def __init__(self,count=30,max_age=.20,pair_bias=None):
        super().__init__();self.count=count;self.max_age=max_age;self.links={};self.pair_bias=pair_bias or {}
    def add(self,beacon):
        old_term=self.term;ranges=super().add(beacon)
        if old_term!=self.term:self.links.clear()
        for r in ranges:
            key=(r.a,r.b);r.distance-=self.pair_bias.get(key,0.)
            if r.distance>0:self.links[key]=r
        return ranges
    def matrices(self,now):
        d=np.full((self.count,self.count),np.nan);w=np.zeros_like(d);np.fill_diagonal(d,0.)
        for (a,b),r in self.links.items():
            age=now-r.effective
            if 0<=age<=self.max_age:
                d[a-1,b-1]=d[b-1,a-1]=r.distance
                w[a-1,b-1]=w[b-1,a-1]=r.quality*math.exp(-age/self.max_age)
        return d,w

class WeightedSMACOFSolver:
    def __init__(self,iterations=15,tolerance=1e-8):
        if not 5<=iterations<=15:raise ValueError('Use 5–15 Guttman iterations')
        self.iterations=iterations;self.tolerance=tolerance;self.metrics={}
    @staticmethod
    def classical_mds(d,w):
        # Floyd-Warshall completion is an initialization only. Missing edges
        # retain zero weights and never become invented observed distances.
        graph=np.where(w>0,d,np.inf);np.fill_diagonal(graph,0.)
        complete=shortest_path(graph,directed=False,method='FW')
        if not np.isfinite(complete).all():raise ValueError('Disconnected graph')
        n=len(d);h=np.eye(n)-np.ones((n,n))/n
        gram=-.5*h@(complete*complete)@h;eigen,vec=np.linalg.eigh(gram)
        order=np.argsort(eigen)[::-1][:2]
        if eigen[order[-1]]<=1e-10:raise ValueError('No planar initialization')
        return vec[:,order]*np.sqrt(eigen[order])
    @staticmethod
    def guttman_b(x,d,w):
        lengths=np.linalg.norm(x[:,None,:]-x[None,:,:],axis=2)
        ratio=np.divide(w*d,lengths,out=np.zeros_like(w),where=lengths>1e-10)
        b=-ratio;np.fill_diagonal(b,ratio.sum(axis=1));return b
    @staticmethod
    def stress(x,d,w):
        errors=d-np.linalg.norm(x[:,None,:]-x[None,:,:],axis=2)
        return float(.5*np.sum(w*errors*errors))
    def solve(self,D,W,X_init=None):
        start=time.perf_counter();d=np.asarray(D,float);w=np.asarray(W,float)
        if d.ndim!=2 or d.shape[0]!=d.shape[1] or d.shape!=w.shape or len(d)<3:raise ValueError('Square D/W with at least three nodes required')
        if not np.isfinite(w).all() or np.any(w<0) or not np.allclose(w,w.T):raise ValueError('Invalid symmetric weights')
        valid=w>0
        if np.any(~np.isfinite(d[valid])) or np.any(d[valid]<=0):raise ValueError('Invalid observed distance')
        if not np.allclose(np.nan_to_num(d),np.nan_to_num(d.T)):raise ValueError('D must be symmetric')
        w=w.copy();np.fill_diagonal(w,0.);d=np.where(w>0,d,0.)
        v=np.diag(w.sum(axis=1))-w
        if np.linalg.matrix_rank(v,tol=1e-8)!=len(d)-1:raise ValueError('Disconnected weighted graph')
        v_pinv=np.linalg.pinv(v,hermitian=True,rcond=1e-10)
        x=None if X_init is None else np.asarray(X_init,float).copy()
        if x is None or x.shape!=(len(d),2) or not np.isfinite(x).all() or np.linalg.norm(x-x.mean(0))<1e-8:x=self.classical_mds(d,w)
        history=[self.stress(x,d,w)]
        for iteration in range(self.iterations):
            candidate=v_pinv@self.guttman_b(x,d,w)@x
            value=self.stress(candidate,d,w)
            if value>history[-1]+1e-8:break
            improvement=history[-1]-value;x=candidate;history.append(value)
            if iteration>=4 and improvement<self.tolerance:break
        self.metrics={'stress':history[-1],'iterations':len(history)-1,'converged':len(history)>=6 and abs(history[-1]-history[-2])<self.tolerance,'stress_history':history,'solver_ms':(time.perf_counter()-start)*1000}
        return x

class OpportunisticKabschAligner:
    """Relative-rest detector; common rigid-body motion is unobservable."""
    def __init__(self,threshold=.03,dwell=.3,anchor_weight=30.):
        self.threshold=threshold;self.dwell=dwell;self.anchor_weight=anchor_weight
        self.previous_d=None;self.history=deque(maxlen=8);self.since={};self.stationary=set()
    def align(self,x,previous,D,W,now):
        if previous is None or self.previous_d is None or self.previous_d.shape!=D.shape:
            self.previous_d=D.copy();self.history.clear();self.since.clear();self.stationary=set();return x-x.mean(0)
        valid=np.isfinite(D)&np.isfinite(self.previous_d)&(W>.15)
        delta=np.where(valid,D-self.previous_d,np.nan);np.fill_diagonal(delta,np.nan)
        self.history.append(delta);self.previous_d=D.copy()
        stack=np.stack(self.history);count=np.sum(np.isfinite(stack),axis=0)
        mean=np.divide(np.nansum(stack,axis=0),count,out=np.zeros_like(D),where=count>0)
        variance=np.divide(np.nansum((stack-mean)**2,axis=0),count,out=np.full_like(D,np.inf),where=count>0)
        stable=(count>=4)&valid&(np.abs(delta)<self.threshold)&(np.sqrt(variance)<self.threshold/2)&(np.abs(mean)<self.threshold/3)
        # Require a mutually supporting stable subgroup, not one noisy edge.
        candidates=set(np.flatnonzero(stable.sum(axis=1)>=2))
        while candidates:
            remove={i for i in candidates if sum(stable[i,j] for j in candidates if j!=i)<2}
            if not remove:break
            candidates-=remove
        for i in range(len(x)):
            if i in candidates:self.since.setdefault(i,now)
            else:self.since.pop(i,None)
        self.stationary={i for i,t in self.since.items() if now-t>=self.dwell}
        if len(self.stationary)<3:self.stationary=set()
        else:
            subset=x[sorted(self.stationary)];s=np.linalg.svd(subset-subset.mean(0),compute_uv=False)
            if s[-1]<.03:self.stationary=set()
        weights=np.array([self.anchor_weight if i in self.stationary else 1. for i in range(len(x))])
        return weighted_kabsch(x,previous,weights)

class SwarmEKFTracker:
    """Per-node CV EKF; XY observation is linear, so EKF reduces to KF here.

    No extra range update is performed: it would double-count the measurements
    used by SMACOF. Correlated SMACOF errors mean covariance is conditional.
    """
    def __init__(self,v_max=1.,gate=13.82):
        self.v_max=v_max;self.gate=gate;self.states={};self.rejected=0
        self.accepted={};self.pending={};self.was_stationary={};self.reacquired=0
        self.status={};self.limited=0
    def update_one(self,node,xy,now,sigma=.03,stationary=False,max_step_dt=None):
        previous=self.states.get(node)
        self.status[node]='updated'
        if previous is None:
            x=np.r_[xy,0.,0.];p=np.diag([sigma*sigma]*2+[.25]*2)
            self.accepted[node]=now;self.pending.pop(node,None)
        else:
            x,p,last=previous;old_xy=x[:2].copy()
            # No long blind extrapolation or immediate reset to a noisy point.
            gap=max(.001,now-last);dt=min(.1,gap)
            x=x.copy();p=p.copy()
            if gap>.5:
                x[2:]=0.;p+=np.diag([.10**2]*2+[.25]*2)
                self.pending.pop(node,None);self.status[node]='reacquiring'
            if self.was_stationary.get(node,False) and not stationary:
                p=p.copy();p[2:,2:]+=np.eye(2)*.25
            if stationary and not self.was_stationary.get(node,False):
                # Stop velocity immediately on corroborated rest. Preserve
                # position uncertainty so a lagging position can still settle.
                x[2:]=0.;p[:2,2:]=0.;p[2:,:2]=0.
                p[:2,:2]+=np.eye(2)*.05**2
            f=np.eye(4);f[:2,2:]=np.eye(2)*dt
            g=np.vstack((np.eye(2)*dt*dt/2,np.eye(2)*dt))
            x=f@x;p=f@p@f.T+g@g.T*(.15 if stationary else 2.)**2
            h=np.c_[np.eye(2),np.zeros((2,2))];r=np.eye(2)*max(.015,sigma)**2
            innovation=xy-h@x;s=h@p@h.T+r
            mahalanobis=float(innovation@np.linalg.solve(s,innovation))
            if mahalanobis<=self.gate:
                k=np.linalg.solve(s,h@p).T;x+=k@innovation
                a=np.eye(4)-k@h;p=a@p@a.T+k@r@k.T
                self.accepted[node]=now;self.pending.pop(node,None)
            else:
                self.rejected+=1
                self.status[node]='gated'
                # A rejected observation provides no support for continued
                # coasting. After a short grace interval, hold the last
                # published position while retaining uncertainty/reacquisition.
                if now-self.accepted.get(node,now)>.12:
                    x[:2]=old_xy;x[2:]=0.
                pending=self.pending.setdefault(node,deque(maxlen=6));pending.append((now,xy.copy()))
                # A real departure from rest must not be rejected forever.
                # Require a coherent sequence and a feasible displacement
                # since the last accepted observation before reacquiring.
                required=6 if stationary else 3
                recent=list(pending)[-required:]
                coherent=len(recent)==required and all(np.linalg.norm(b[1]-a[1])<=self.v_max*(b[0]-a[0])+2*sigma for a,b in zip(recent,recent[1:]))
                elapsed=now-self.accepted.get(node,now)
                if coherent and np.linalg.norm(innovation)<=self.v_max*elapsed+3*sigma:
                    velocity=np.zeros(2) if stationary else (recent[-1][1]-recent[0][1])/max(.001,recent[-1][0]-recent[0][0])
                    x=np.r_[xy,velocity];p=np.diag([sigma*sigma]*2+[.25]*2)
                    self.accepted[node]=now;self.pending.pop(node,None);self.reacquired+=1
                    self.status[node]='reacquiring'
            if stationary:
                hz=np.c_[np.zeros((2,2)),np.eye(2)];rz=np.eye(2)*.02**2
                sz=hz@p@hz.T+rz;kz=np.linalg.solve(sz,hz@p).T;x-=kz@(hz@x)
                a=np.eye(4)-kz@hz;p=a@p@a.T+kz@rz@kz.T
            speed=np.linalg.norm(x[2:])
            if speed>self.v_max:x[2:]*=self.v_max/speed
            displacement=x[:2]-old_xy;length=np.linalg.norm(displacement)
            limit=self.v_max*(dt if max_step_dt is None else min(dt,max(.001,max_step_dt)))
            if length>limit:
                x[:2]=old_xy+displacement*(limit/length)
                # The bounded estimate has not reached the observation yet.
                remaining=displacement*(1.-limit/length)
                p[:2,:2]+=np.outer(remaining,remaining)
                self.limited+=1;self.status[node]='limited'
        self.was_stationary[node]=stationary
        self.states[node]=(x,p,now);return x[:2].copy()
    def update(self,xy,now,sigma=.03,stationary=()):
        return np.array([self.update_one(i,p,now,sigma,i in stationary) for i,p in enumerate(xy)])

class SwarmSimulationAndVisualizer:
    def __init__(self,count=30,seed=731,loss=.20,noise_std=.025,nlos_probability=.06):
        self.count=count;self.rng=np.random.default_rng(seed);self.loss=loss;self.noise_std=noise_std;self.nlos_probability=nlos_probability
        self.truth=self.rng.uniform(-1.5,1.5,(count,2));self.velocity=np.zeros((count,2));self.change=np.zeros(count)
        self.rates=1+self.rng.normal(0,15e-6,count);self.offset=self.rng.integers(0,1<<40,count,dtype=np.int64)
        self.received=[{} for _ in range(count)];self.previous_tx={};self.seq=0
        self.assembler=AsymmetricDSTWRAssembler(count,max_age=.24);self.solver=WeightedSMACOFSolver();self.aligner=OpportunisticKabschAligner();self.tracker=SwarmEKFTracker()
        self.previous=None;self.previous_ids=None;self.errors=[];self.times=[];self.durations=[];self.coverage=[];self.skipped=0
    def move(self,t,dt):
        for i in range(self.count):
            if t>=self.change[i]:
                self.change[i]=t+self.rng.uniform(.3,1.8)
                if self.rng.random()<.55:self.velocity[i]=0.
                else:
                    angle=self.rng.uniform(0,2*np.pi);self.velocity[i]=self.rng.uniform(.05,.85)*np.array([np.cos(angle),np.sin(angle)])
        self.truth+=self.velocity*dt
        for axis in range(2):
            outside=np.abs(self.truth[:,axis])>2.;self.velocity[outside,axis]*=-1;self.truth[:,axis]=np.clip(self.truth[:,axis],-2,2)
    def broadcast_cycle(self,t):
        # Thirty fixed slots, even if the requested demo uses fewer nodes.
        for i in range(self.count):
            physical=t+(30*i//self.count)*.0013;clock=int(physical/DTU*self.rates[i]+int(self.offset[i]))
            tx=((clock&~511)+16400)&MASK40;peers={}
            for j,(rx,seq,gap) in self.received[i].items():
                age=(tx-rx)&MASK40
                if 0<age<2**32:peers[j+1]=(age,seq,gap)
            old=self.previous_tx.get(i)
            b=Beacon(i+1,self.count,1,self.seq,0x53574131,i+1,1,tx,old[0] if old else 0,old[1] if old else 0,bool(old),peers,physical)
            if self.rng.random()>=self.loss:self.assembler.add(b)
            self.previous_tx[i]=(self.seq,tx)
            # Convert the quantized antenna TX timestamp back into common time.
            raw_actual=(clock&~511)+16400;actual=(raw_actual-int(self.offset[i]))*DTU/self.rates[i]
            for j in range(self.count):
                if j==i or self.rng.random()<self.loss:continue
                distance=np.linalg.norm(self.truth[i]-self.truth[j]);nlos=self.rng.random()<self.nlos_probability
                noise=self.rng.normal(0,self.noise_std)+(self.rng.uniform(.15,.70) if nlos else 0.)
                rx=int((actual+(distance+noise)/C)/DTU*self.rates[j]+int(self.offset[j]))&MASK40
                self.received[j][i]=(rx,self.seq,18 if nlos else 3)
        self.seq=(self.seq+1)&65535
    @staticmethod
    def relative_rmse(estimate,truth):
        a=estimate-estimate.mean(0);b=truth-truth.mean(0)
        u,_,vt=np.linalg.svd(a.T@b);aligned=a@(u@vt)+truth.mean(0)
        return float(np.sqrt(np.mean(np.sum((aligned-truth)**2,axis=1)))),aligned
    def run(self,seconds=10.,headless=False,output=None):
        import matplotlib
        if headless:matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,(ax,history)=plt.subplots(1,2,figsize=(12,5));ax.set_aspect('equal');ax.set(xlim=(-2.5,2.5),ylim=(-2.5,2.5),xlabel='x (m)',ylabel='y (m)',title='SIMULATION — relative alignment for scoring only')
        truth_plot=ax.scatter([],[],marker='x',label='True');estimate_plot=ax.scatter([],[],s=18,label='Estimated');ax.legend()
        curve,=history.plot([],[]);history.set(xlabel='Time (s)',ylabel='Relative shape RMSE (cm)',title='20% loss per RX + 6% NLOS injections')
        estimate=None;last=time.perf_counter()
        with threadpool_limits(limits=1):
            for frame in range(int(seconds/.039)):
                start=time.perf_counter();t=frame*.039;self.move(t,.039);self.broadcast_cycle(t)
                d,w=self.assembler.matrices(t+.039)
                try:
                    _,labels=connected_components(w>0,directed=False)
                    ids=np.flatnonzero(labels==np.argmax(np.bincount(labels)))
                    if len(ids)<3:raise ValueError('No connected planar component')
                    subd=d[np.ix_(ids,ids)];subw=w[np.ix_(ids,ids)]
                    reference=self.previous[ids] if self.previous is not None else None
                    seed=reference
                    if reference is not None and not np.isfinite(reference).all():
                        seed=self.solver.classical_mds(subd,subw);common=np.isfinite(reference).all(1)
                        if common.sum()>=3:
                            a=seed[common];b=reference[common];u,_,vt=np.linalg.svd((a-a.mean(0)).T@(b-b.mean(0)))
                            seed=(seed-a.mean(0))@(u@vt)+b.mean(0)
                        reference=seed.copy()
                    if self.previous_ids is None or not np.array_equal(ids,self.previous_ids):
                        self.aligner=OpportunisticKabschAligner()
                        # Preserve the known frame across temporary missing nodes.
                        if reference is not None:self.aligner.previous_d=subd.copy()
                    x=self.solver.solve(subd,subw,seed);x=self.aligner.align(x,reference,subd,subw,t)
                    if self.previous is None:self.previous=np.full((self.count,2),np.nan)
                    self.previous[ids]=x;self.previous_ids=ids.copy()
                    residual=np.linalg.norm(x[:,None]-x[None,:],axis=2)-np.nan_to_num(subd)
                    sigma=max(.03,float(np.sqrt(np.sum(subw*residual**2)/max(subw.sum(),1))))
                    estimate=np.array([self.tracker.update_one(int(node),x[k],t,sigma,k in self.aligner.stationary) for k,node in enumerate(ids)])
                    error,aligned=self.relative_rmse(estimate,self.truth[ids])
                    self.errors.append(error);self.times.append(t);self.coverage.append(len(ids)/self.count);self.durations.append(time.perf_counter()-start)
                except ValueError:self.skipped+=1;continue
                if not headless or frame==int(seconds/.039)-1:
                    truth_plot.set_offsets(self.truth);estimate_plot.set_offsets(aligned)
                    curve.set_data(self.times,np.array(self.errors)*100);history.relim();history.autoscale_view()
                    if not headless:plt.pause(max(.001,.039-(time.perf_counter()-start)))
        result={'simulation_only':True,'nodes':self.count,'seconds':seconds,'loss_per_receive':self.loss,'nlos_probability':self.nlos_probability,'solved_frames':len(self.errors),'skipped_frames':self.skipped,'mean_node_coverage':float(np.mean(self.coverage)) if self.coverage else 0.,'mean_relative_rmse_cm':float(np.mean(self.errors)*100) if self.errors else None,'p95_relative_rmse_cm':float(np.percentile(self.errors,95)*100) if self.errors else None,'pipeline_p95_ms':float(np.percentile(self.durations,95)*1000) if self.durations else None,'ekf_rejections':self.tracker.rejected,'scoring':'Largest connected component only; per-frame orthogonal alignment removes unobservable translation/rotation and initial chirality; not absolute accuracy.'}
        print(json.dumps(result,indent=2))
        if output:
            output=Path(output);output.mkdir(parents=True,exist_ok=True)
            (output/'metrics.json').write_text(json.dumps(result,indent=2));fig.savefig(output/'simulation.png',dpi=150)
        if not headless:plt.show()
        else:plt.close(fig)
        return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=float,default=30);p.add_argument('--nodes',type=int,default=30);p.add_argument('--headless',action='store_true');p.add_argument('--output',type=Path,default=Path('build/swarm-simulation'));p.add_argument('--seed',type=int,default=731)
    a=p.parse_args()
    if not 3<=a.nodes<=30 or not 0<a.seconds<=3600:p.error('nodes 3–30; seconds (0,3600]')
    SwarmSimulationAndVisualizer(a.nodes,a.seed).run(a.seconds,a.headless,a.output)
if __name__=='__main__':main()
