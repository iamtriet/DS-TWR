"""Replay motion with the full pre-motion history; no truth supplied to filter."""
import argparse,json
from pathlib import Path
from collections import defaultdict,deque
from types import SimpleNamespace
import numpy as np
from threadpoolctl import threadpool_limits
from swarm_cooperative_tracker import CooperativeRangeTracker
from uwb3d_core import Range

def run(source,cutoff,output):
    tracker=CooperativeRangeTracker(origin=6);history=defaultdict(lambda:deque(maxlen=120))
    seen={};points=[];recorded=[];epochs=[];motion=[]
    with threadpool_limits(limits=1):
        for line in source.open():
            s=json.loads(line).get('state')
            if not isinstance(s,dict) or s.get('ids')!=list(range(1,7)):continue
            if s['time']>cutoff:break
            edges=[Range(**r) for r in s['edges']]
            for r in edges:
                key=(r.a,r.b)
                if seen.get(key)!=r.effective:seen[key]=r.effective;history[key].append((r.effective,r.distance))
            if epochs and s['time']-epochs[-1]<.07:continue
            nodes={n['id']:SimpleNamespace(imu=n.get('imu')) for n in s['nodes']}
            p,m=tracker.solve(s['ids'],np.array(s['matrix']),edges,nodes,history,s['time'])
            points.append(p);recorded.append([s['positions'][str(i)] for i in range(1,7)])
            epochs.append(s['time']);motion.append(bool(nodes[2].imu['moving']))
    moving=np.flatnonzero(motion);first,last=moving[0],moving[-1]
    def shifts(data):
        x=np.array(data);base=np.median(x[max(0,first-10):first],axis=0)
        return {str(i+1):dict(max_shift_m=float(np.max(np.linalg.norm(x[first:last+1,i]-base[i],axis=1))),at_stop_shift_m=float(np.linalg.norm(x[last,i]-base[i]))) for i in range(6)}
    report=dict(frames=len(epochs),recorded=shifts(recorded),replayed=shifts(points),limitation='Displacement during user-identified node 2 motion, not absolute position error')
    output.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('cutoff',type=float);p.add_argument('output',type=Path);a=p.parse_args();run(a.source,a.cutoff,a.output)
