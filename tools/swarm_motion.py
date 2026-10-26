"""Explicit stationary-reference mode for controlled moving-node trials.

Reference distances are surveyed automatically, but reference roles are chosen
by the operator. The reference gauge survives a mobile-node outage.
"""
from itertools import combinations
import numpy as np
from scipy.optimize import least_squares


class MotionReferenceSolver:
    def __init__(self,reference_nodes,direct_ranges=False):
        if len(reference_nodes)!=3:raise ValueError('Motion trial needs exactly three stationary reference nodes')
        self.nodes=tuple(reference_nodes);self.positions={};self.last={}
        from swarm_range_tracker import RangeDomainTracker
        self.tracker=RangeDomainTracker() if direct_ranges else None

    def solve(self,ids,edges,history,now,rest_nodes=()):
        if not all(n in ids for n in self.nodes):raise ValueError('Stationary reference offline; retaining previous gauge')
        links={tuple(sorted((r.a,r.b))):r for r in edges}
        if not self.positions:
            distances={}
            for pair in combinations(self.nodes,2):
                samples=[(t,d) for t,d in history.get(tuple(sorted(pair)),()) if now-t<1.2]
                if len(samples)<15 or samples[-1][0]-samples[0][0]<.7:
                    raise ValueError('Surveying three selected stationary nodes (0.7 s)')
                distances[pair]=float(np.median([d for _,d in samples]))
            a,b,c=self.nodes;ab=distances[(a,b)];ac=distances[(a,c)];bc=distances[(b,c)]
            if ab<.1:raise ValueError('Reference baseline too short')
            along=(ab*ab+ac*ac-bc*bc)/(2*ab);height2=ac*ac-along*along
            if height2<.06**2:raise ValueError('Reference geometry nearly collinear or inconsistent')
            triangle=np.array([[0.,0.],[ab,0.],[along,np.sqrt(height2)]])
            triangle-=triangle.mean(0)
            self.positions=dict(zip(self.nodes,triangle))
        residual=[];missing_reference=[]
        for pair in combinations(self.nodes,2):
            r=links.get(tuple(sorted(pair)))
            if r is None:
                missing_reference.append(pair);continue
            residual.append(np.linalg.norm(self.positions[pair[0]]-self.positions[pair[1]])-r.distance)
        core_rms=float(np.sqrt(np.mean(np.square(residual)))) if residual else 0.
        # Once surveyed, reference-reference measurements are a health check,
        # not inputs to the mobile trilateration. A bad health check must not
        # discard three independent fresh mobile ranges. Continue ONLY as a
        # conditional estimate in the operator-selected fixed reference frame.
        degraded=bool(missing_reference) or core_rms>.20
        warning=('Reference geometry unverified; estimate assumes selected references remain fixed'
                 if degraded else '')
        points=[];unavailable={};sigma={};quality={};stationary=set(self.nodes)
        for node in ids:
            if node in self.positions:
                points.append(self.positions[node]);sigma[node]=.03
                quality[node]={'fixed_reference':True,'reference_geometry_verified':not degraded}
                continue
            records=[links.get(tuple(sorted((node,a)))) for a in self.nodes]
            seed=self.last.get(node,np.mean(list(self.positions.values()),axis=0))
            if any(r is None for r in records):
                unavailable[node]='Mobile node lacks three fresh ranges';points.append(seed);continue
            anchors=np.array([self.positions[a] for a in self.nodes])
            distances=np.array([r.distance for r in records])
            weights=np.sqrt([max(.02,r.quality if r.quality is not None else .25) for r in records])
            if self.tracker is not None and node in self.tracker.states:
                point,uncertainty,details=self.tracker.update(node,anchors,records,seed,node in rest_nodes)
                self.last[node]=point;points.append(point);sigma[node]=uncertainty;quality[node]=details
                continue
            # Subtract squared range equations for a geometry-based seed.
            # A centered centroid can be ~1e-17 rather than exactly zero.
            # TRF then starts with a vanishing trust radius and can report
            # convergence without leaving that point.
            matrix=2*(anchors[1:]-anchors[0])
            rhs=distances[0]**2-distances[1:]**2+np.sum(anchors[1:]**2,axis=1)-np.sum(anchors[0]**2)
            linear=np.linalg.lstsq(matrix,rhs,rcond=None)[0]
            seeds=[np.zeros(2) if np.linalg.norm(seed)<1e-8 else seed,linear]
            fits=[least_squares(lambda p:weights*(np.linalg.norm(p-anchors,axis=1)-distances),start,
                                loss='soft_l1',f_scale=.05,max_nfev=40) for start in seeds]
            fit=min(fits,key=lambda f:f.cost if f.success else np.inf)
            # Near-collinear references can admit two well-separated minima.
            # Small RF changes must not switch branches merely because the
            # other minimum is marginally cheaper. Only prefer continuity
            # among fits indistinguishable at a 5 cm range-noise scale.
            comparable=[f for f in fits if f.success and f.cost<=fit.cost+len(records)*.05**2/2]
            ambiguous=any(np.linalg.norm(f.x-fit.x)>.20 for f in comparable)
            if node in self.last and ambiguous:
                fit=min(comparable,key=lambda f:np.linalg.norm(f.x-self.last[node]))
            quality[node]={'ambiguous_branches':ambiguous}
            errors=np.linalg.norm(fit.x-anchors,axis=1)-distances
            rms=float(np.sqrt(np.mean(errors**2)))
            delta=fit.x-anchors;unit=delta/np.maximum(np.linalg.norm(delta,axis=1,keepdims=True),1e-6)
            eigen=np.linalg.eigvalsh(unit.T@(weights[:,None]**2*unit))
            if not fit.success or rms>.20 or eigen[0]<.03:
                unavailable[node]='Mobile ranges inconsistent or weak geometry';points.append(seed);continue
            self.last[node]=fit.x.copy();points.append(fit.x)
            sigma[node]=min(.20,max(.03,rms)/np.sqrt(eigen[0]))
            if self.tracker is not None:
                point,sigma[node],details=self.tracker.update(node,anchors,records,fit.x,node in rest_nodes)
                points[-1]=point;quality[node].update(details)
        return np.c_[points,np.zeros(len(points))],dict(rms=core_rms,uncertainty=max(max(sigma.values(),default=.03),core_rms),
            condition=None,converged=True,stationary=sorted(stationary),unavailable_nodes=unavailable,
            reference_degraded=degraded,reference_warning=warning,
            range_domain=self.tracker is not None,
            node_sigma=sigma,node_quality=quality,observability='operator-selected stationary reference; planar relative gauge')
