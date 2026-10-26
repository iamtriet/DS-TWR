"""Display-only position history. Never used as mapping evidence."""
from collections import deque
import math


class LiveTrails:
    def __init__(self, limit=6000):
        self.limit=limit;self.rows={};self.frame=None;self.last_stamp=None

    def update(self, snapshot, generation, now):
        origin=snapshot.get('metrics',{}).get('origin_node')
        if not origin:return
        frame=(generation,origin,snapshot.get('network',{}).get('term'))
        if self.frame!=frame:
            self.rows.clear();self.last_stamp=None;self.frame=frame
        stamp=snapshot.get('time',0)
        if self.last_stamp==stamp or now-snapshot.get('published_at',stamp)>.5:return
        self.last_stamp=stamp
        nodes={int(n['id']):n for n in snapshot.get('nodes',[])}
        for key,p in snapshot.get('positions',{}).items():
            node=int(key)
            if node in snapshot.get('position_reacquired',[]):continue
            if str(node) in snapshot.get('position_rejections',{}) or snapshot.get('node_states',{}).get(node,snapshot.get('node_states',{}).get(str(node)))=='outlier-rejected':continue
            if node==origin or len(p)<2 or not all(math.isfinite(v) for v in p[:2]):continue
            if now-nodes.get(node,{}).get('last',0)>.5:continue
            rows=self.rows.setdefault(node,deque(maxlen=self.limit))
            previous=rows[-1] if rows else None
            if previous and stamp-previous['t']<.1:continue
            gap=previous is None or stamp-previous['t']>.5 or stamp<=previous['t']
            segment=(previous['segment'] if previous else 0)+int(gap)
            # Large jumps remain isolated markers, never apparent travel corridors.
            if previous and math.dist(p[:2],previous['xy'])>.5:segment+=1
            rows.append(dict(t=stamp,xy=list(p[:2]),segment=segment))
