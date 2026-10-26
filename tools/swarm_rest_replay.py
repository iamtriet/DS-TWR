"""Replay a user-confirmed rest recording; jitter is not absolute accuracy."""
import argparse,json,sys
from pathlib import Path
from collections import defaultdict,deque
from types import SimpleNamespace
import numpy as np
from threadpoolctl import threadpool_limits
from swarm_cooperative_tracker import CooperativeRangeTracker
from uwb3d_core import Range

def run(source,output):
    tracker=CooperativeRangeTracker(origin=6)
    history=defaultdict(lambda:deque(maxlen=100));seen={};series=defaultdict(list)
    original=defaultdict(list);ranges=defaultdict(list);epochs=[];sigma=[];count=0
    with threadpool_limits(limits=1):
        for line in source.open():
            record=json.loads(line);s=record.get('state')
            if not isinstance(s,dict) or s.get('ids')!=[1,2,3,4,5,6]:continue
            now=s['time'];edges=[Range(**r) for r in s['edges']]
            for r in edges:
                key=tuple(sorted((r.a,r.b)))
                if seen.get(key)!=r.effective:
                    seen[key]=r.effective;history[key].append((r.effective,r.distance));ranges[key].append(r.distance)
            if epochs and now-epochs[-1]<.07:continue
            nodes={n['id']:SimpleNamespace(imu=n.get('imu')) for n in s['nodes']}
            points,meta=tracker.solve(s['ids'],np.array(s['matrix']),edges,nodes,history,now)
            epochs.append(now);sigma.append(meta['uncertainty']);count+=1
            for i,node in enumerate(s['ids']):
                series[node].append(points[i,:2]);original[node].append(s['positions'][str(node)][:2])
    def metrics(values):
        # Omit first five seconds while each replay initializes its own state.
        x=np.array(values)[np.array(epochs)>epochs[0]+5]
        return dict(radial_std_cm=float(100*np.sqrt(np.mean(np.sum((x-x.mean(0))**2,axis=1)))),
                    span_cm=(100*np.ptp(x,axis=0)).tolist())
    report=dict(frames=count,duration_s=epochs[-1]-epochs[0],max_sigma_m=max(sigma),
        nodes={n:dict(recorded=metrics(original[n]),replayed=metrics(series[n])) for n in series},
        links={f'{a}-{b}':dict(std_cm=float(np.std(v)*100),median_m=float(np.median(v)),p5_p95_m=np.percentile(v,[5,95]).tolist()) for (a,b),v in sorted(ranges.items())},
        limitation='Stationary dispersion only; no ground truth positions or absolute accuracy measurement')
    output.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('output',type=Path);a=p.parse_args();run(a.source,a.output)
