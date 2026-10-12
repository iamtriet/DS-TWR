"""Top-down XY rendering; only surveyed anchors and solved tag positions are plotted."""
import math
from collections import deque
import time
import tkinter as tk

TAG_COLORS = ('#42dcc6', '#ffbd69', '#ba9eff', '#ff829d', '#72b8ff', '#b9df67', '#ed98e3', '#ffffff')


class Viewport:
    def __init__(self): self.center = (0., 0.); self.span = 4.

    def fit(self, points):
        if not points: return
        lo = [min(p[i] for p in points) for i in (0,1)]
        hi = [max(p[i] for p in points) for i in (0,1)]
        self.center = tuple((a+b)/2 for a,b in zip(lo,hi))
        self.span = max(.5,hi[0]-lo[0],hi[1]-lo[1])*1.45

    def scale(self, width, height): return max(1,min(width-100,height-100))/self.span

    def pixel(self, xyz, width, height):
        s = self.scale(width,height)
        return width/2+(xyz[0]-self.center[0])*s, height/2-(xyz[1]-self.center[1])*s

    def world(self, x, y, width, height):
        s = self.scale(width,height)
        return self.center[0]+(x-width/2)/s, self.center[1]-(y-height/2)/s


class TopDownMap(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent,bg='#101f2c',highlightthickness=0,height=360)
        self.viewport = Viewport()
        self.profile_key = None
        self.trails = {}
        self.last_stamp = {}
        self.drag = None
        self.bind('<ButtonPress-1>', self.begin_pan)
        self.bind('<B1-Motion>', self.pan)
        self.bind('<MouseWheel>', lambda e:self.zoom(.85 if e.delta>0 else 1/.85))

    def begin_pan(self,event): self.drag = (event.x,event.y,self.viewport.center)

    def pan(self,event):
        if self.drag:
            x,y,center = self.drag
            s = self.viewport.scale(self.winfo_width(),self.winfo_height())
            self.viewport.center = (center[0]-(event.x-x)/s,center[1]+(event.y-y)/s)

    def zoom(self,factor): self.viewport.span = min(10000,max(.1,self.viewport.span*factor))

    def reset(self): self.profile_key = None; self.trails.clear(); self.last_stamp.clear()

    def render(self, model, running=False):
        if hasattr(model,'display'): model = model.display()
        self.delete('all')
        width,height = max(200,self.winfo_width()),max(160,self.winfo_height())
        if model is None or model.profile is None:
            # This grid is a visual background, not an assumed room geometry.
            for x in range(0,width,40): self.create_line(x,0,x,height,fill='#192d3b')
            for y in range(0,height,40): self.create_line(0,y,width,y,fill='#192d3b')
            automatic = getattr(model,'automatic',False)
            self.create_text(width/2,height/2-35,text='Waiting for first position' if automatic else 'Set your anchor positions',fill='#e4edf3',font=('Segoe UI',20,'bold'))
            self.create_text(width/2,height/2+10,text=model.reason if automatic else 'Enter measured X, Y and height for the four anchors.\nTags appear here when their positions can be calculated.',
                             fill='#9eb4c5',font=('Segoe UI',11),justify='center',width=width-50)
            self.create_text(18,18,anchor='nw',text='TOP-DOWN  ·  XY',fill='#8da9be',font=('Segoe UI',10,'bold'))
            return {}
        points = [model.profile['anchors'][a['name']] for a in model.plan['anchors']]
        automatic = getattr(model,'automatic',False)
        key = tuple(a['name'] for a in model.plan['anchors']) if automatic else tuple(tuple(p) for p in points)
        if key != self.profile_key:
            self.viewport.fit(points); self.profile_key = key; self.trails.clear(); self.last_stamp.clear()
        scale = self.viewport.scale(width,height)
        step = 10**math.floor(math.log10(max(.01,70/scale)))
        if step*scale<35: step*=5
        left,bottom = self.viewport.world(0,height,width,height)
        right,top = self.viewport.world(width,0,width,height)
        for axis,start,end in ((0,left,right),(1,bottom,top)):
            for n in range(math.ceil(start/step),math.floor(end/step)+1):
                v=n*step
                p=self.viewport.pixel((v,0) if axis==0 else (0,v),width,height)
                color='#365066' if n==0 else '#1e3445'
                if axis==0:
                    self.create_line(p[0],0,p[0],height,fill=color)
                    self.create_text(p[0]+3,height-8,anchor='sw',text=f'{v:g}',fill='#718da3',font=('Segoe UI',8))
                else:
                    self.create_line(0,p[1],width,p[1],fill=color)
                    self.create_text(5,p[1]-3,anchor='sw',text=f'{v:g}',fill='#718da3',font=('Segoe UI',8))
        for anchor,xyz in zip(model.plan['anchors'],points):
            x,y = self.viewport.pixel(xyz,width,height)
            self.create_rectangle(x-8,y-8,x+8,y+8,fill='#cbd9e5',outline='#ffffff',width=2)
            self.create_text(x+14,y-5,anchor='w',text=anchor['id'],fill='#f1f6fa',font=('Segoe UI',10,'bold'))
            self.create_text(x+14,y+11,anchor='w',text=f'z {xyz[2]:.2f} m',fill='#8da9be',font=('Segoe UI',8))
        if getattr(model,'cooperative',False):
            for a,b in model.selected:
                if a in model.positions and b in model.positions:
                    p=self.viewport.pixel(model.positions[a],width,height)
                    q=self.viewport.pixel(model.positions[b],width,height)
                    self.create_line(*p,*q,fill='#8063ac' if a.startswith('B') and b.startswith('B') else '#36556a',dash=(3,4))
        results = {}
        for index,tag in enumerate(model.plan['tags']):
            color=TAG_COLORS[index%len(TAG_COLORS)]
            try:
                xyz,residual=model.locate(tag['id'])
                stamp=model.tag_stamps[tag['id']] if getattr(model,'cooperative',False) else max(model.ranges[a['id'],tag['id']][1] for a in model.plan['anchors'])
                trail=self.trails.setdefault(tag['id'],deque(maxlen=160))
                if self.last_stamp.get(tag['id'])!=stamp:
                    trail.append((stamp,xyz));self.last_stamp[tag['id']]=stamp
                # A changing relative frame cannot carry world-space trails.
                visible=[] if automatic else [p for t,p in trail if time.monotonic()-t<15]
                if len(visible)>1:
                    coordinates=[c for p in visible for c in self.viewport.pixel(p,width,height)]
                    self.create_line(*coordinates,fill=color,width=2)
                x,y=self.viewport.pixel(xyz,width,height)
                self.create_oval(x-14,y-14,x+14,y+14,outline=color,width=1,dash=(4,3) if getattr(model,'held',False) else ())
                self.create_oval(x-7,y-7,x+7,y+7,fill=color,outline='#ffffff',width=1)
                self.create_text(x+18,y-5,anchor='w',text=tag['id'],fill=color,font=('Segoe UI',11,'bold'))
                results[tag['id']]=(xyz,None)
            except ValueError as exc:
                self.trails.pop(tag['id'],None)
                results[tag['id']]=(None,str(exc))
        self.create_text(16,15,anchor='nw',text='TOP-DOWN  ·  metres  ·  +Y ↑  +X →',fill='#acc3d4',font=('Segoe UI',10,'bold'))
        self.create_text(width-14,15,anchor='ne',text='Drag to pan  /  scroll to zoom',fill='#819db2',font=('Segoe UI',9))
        if getattr(model,'held',False):
            subject='cooperative frame' if getattr(model,'cooperative',False) else 'anchor survey'
            self.create_text(16,40,anchor='nw',text=f'HELD · {subject} {model.age:.1f}s old',
                             fill='#ffbd69',font=('Segoe UI',11,'bold'))
        bar = step*scale
        self.create_line(width-25-bar,height-25,width-25,height-25,fill='#cbd9e5',width=3)
        self.create_text(width-25-bar/2,height-32,anchor='s',text=f'{step:g} m',fill='#cbd9e5',font=('Segoe UI',9))
        return results
