"""Control Center's cooperative multi-tag view and selected ranging graph."""
import time
from collections import deque
from tkinter import ttk
from multi_anchor_view import MultiAnchorView
from cooperative_tracking import CooperativeDisplay


class CooperativeView(MultiAnchorView):
    def __init__(self,parent,prepare,start,stop):
        super().__init__(parent,prepare,start,stop,automatic=True)
        for child in self.winfo_children():
            if isinstance(child,ttk.Label) and child.cget('text')=='Auto-anchor / Relative top-down':
                child.configure(text='Multi-tag / Sparse cooperative tracking')
        self.auto_mode.set('2D / ALL boards in the same plane')

    def set_plan(self,plan):
        super().set_plan(plan)
        self.model=CooperativeDisplay(plan,mode='xy' if self.auto_mode.get().startswith('2D') else 'xyz')
        self.rows={}
        for row in self.table.get_children(): self.table.delete(row)
        for column,title in (('anchor','Node A'),('tag','Node B')):self.table.heading(column,text=title)
        self.edge_text.set('Dynamic neighbors: collecting initial ranges')

    def slot(self,value):
        key=(value['a'],value['b'])
        if key not in self.rows:
            self.rows[key]=dict(distance=None,filtered=None,mode='COOPERATIVE',last=None,
                               times=deque(maxlen=100),success=0,failed=0,tag=value['b'])
            self.table.insert('','end',iid='/'.join(key))
        self.record_tags([[value['a'],value['b'],value['distance'],value['stamp']]])
        self.rows[key]['mode']='COOPERATIVE'

    def frame(self,value):
        self.model.accept(value)
        pairs=' | '.join(f"{tag}: {', '.join(peers)}" for tag,peers in value['neighbors'].items())
        self.edge_text.set(f"Neighbors — {pairs}\n{value['slots']} scheduled slots · {value['duration']:.2f}s/cycle · {len(value['selected'])} active graph edges")
        selected={tuple(k) for k in value['selected']}
        for key,row in self.rows.items(): row['mode']='SELECTED' if key in selected else 'PROBE / OLD'

    def draw_position(self):
        if not isinstance(self.model,CooperativeDisplay): return
        if time.monotonic()-self.last_draw<.08:return
        self.last_draw=time.monotonic()
        shown=self.model.display() if self.model else None
        found=self.canvas.render(shown,self.is_running) or {}
        if not self.plan:return
        now=time.monotonic()
        for tag in self.plan['tags']:
            tid=tag['id'];xyz,error=found.get(tid,(None,self.model.reason))
            neighbors=', '.join(self.model.neighbors.get(tid,[]))
            status=f"X {xyz[0]:.2f}  Y {xyz[1]:.2f}\nRelative Z {xyz[2]:.2f} m" if xyz else error
            if xyz:
                status+=f"\nPosition age {max(0.,now-shown.tag_stamps[tid]):.1f}s\nFit RMS {100*shown.results[tid][1]:.1f} cm"
                if shown.held:status+='\nHELD'
            motion=self.model.motion[tag['name']].state(now)
            self.tag_labels[tid].configure(text=f"● {tid}\n{tag['name']} · {motion}\nPeers: {neighbors}\n{status}")
