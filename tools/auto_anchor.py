"""Repeated relative anchor surveys, bracketed around each tag snapshot.

The frame is attached to A1/A2/A3, not gravity or the building. Distances
cannot observe rigid translation, rotation or reflection of the whole fleet.
"""
import asyncio
import itertools
import math
import re
import time
from multi_anchor import MultiAnchorFleet, RETRYABLE_RANGE_CODES
from uart_monitor import parse_uwb
from tracking_estimation import TrackingEstimate, position, least_squares
from types import SimpleNamespace
from collections import deque

MAX_EPOCH_SECONDS = 5.0
MAX_EDGE_CHANGE_M = .05


def relative_layout(edges, mode='xyz'):
    """Six edges in order 01,02,03,12,13,23 -> canonical tetrahedron."""
    if len(edges) != 6 or any(v is None or not math.isfinite(v) or not .05 < v <= 1000 for v in edges):
        raise ValueError('Waiting for six valid anchor distances (> 5 cm)')
    d01,d02,d03,d12,d13,d23 = edges
    scale = max(edges)
    for x,y,z in ((d01,d02,d12),(d01,d03,d13),(d02,d03,d23),(d12,d13,d23)):
        # Planar mode fits noisy edges jointly below and checks every residual.
        # Exact triangle rejection here would discard even a centimetre of
        # noise on a collinear triplet, despite a sound four-anchor 2D fit.
        if mode != 'xy' and max(x,y,z)*2 >= x+y+z:
            raise ValueError('Inconsistent anchor distances: triangle inequality')
    x2 = (d01*d01+d02*d02-d12*d12)/(2*d01)
    y2sq = d02*d02-x2*x2
    if y2sq <= (.08*scale)**2:
        raise ValueError('A1/A2/A3 nearly collinear: spread anchors apart')
    y2 = math.sqrt(y2sq)
    x3 = (d01*d01+d03*d03-d13*d13)/(2*d01)
    y3 = (d02*d02+d03*d03-d23*d23-2*x2*x3)/(2*y2)
    if mode == 'xy':
        # Explicit user assumption: every anchor AND tag occupies one plane.
        # Fit all six edges, retaining one redundant planar consistency check.
        coords = [[0.,0.,0.],[d01,0.,0.],[x2,y2,0.],[x3,y3,0.]]
        variables = ((1,0),(2,0),(2,1),(3,0),(3,1))
        pairs = list(itertools.combinations(range(4),2))
        for _ in range(15):
            jac, errors = [], []
            for (a,b), measured in zip(pairs,edges):
                predicted = math.dist(coords[a],coords[b])
                if predicted < .01: raise ValueError('Overlapping anchors in planar fit')
                jac.append([((coords[a][axis]-coords[b][axis])/predicted)*
                            (1 if node==a else -1 if node==b else 0) for node,axis in variables])
                errors.append(measured-predicted)
            step = least_squares(jac,errors)
            for (node,axis),delta in zip(variables,step): coords[node][axis] += delta
            if sum(v*v for v in step)<1e-12: break
        if coords[1][0] <= .05 or coords[2][1] < .08*scale:
            raise ValueError('Weak planar geometry; spread the first three anchors apart')
        if max(abs(math.dist(coords[a],coords[b])-d) for (a,b),d in zip(pairs,edges))>.05:
            raise ValueError('Anchor ranges do not fit the selected common plane within 5 cm')
        return coords
    if mode != 'xyz': raise ValueError('Unknown geometry mode')
    z3sq = d03*d03-x3*x3-y3*y3
    if z3sq <= (.08*scale)**2:
        raise ValueError('Flat or inconsistent 3D layout: move one anchor out of the others’ plane')
    coords = [[0.,0.,0.],[d01,0.,0.],[x2,y2,0.],[x3,y3,math.sqrt(z3sq)]]
    # Reject all near-coplanar tetrahedra, including skinny choices of baseline.
    normalized_volume = d01*y2*math.sqrt(z3sq)/(scale**3)
    if normalized_volume < .025:
        raise ValueError('Weak 3D anchor geometry: spread anchors in three dimensions')
    return coords


class AutoAnchorFleet(MultiAnchorFleet):
    previous_survey = None
    survey_reverse = False

    async def ensure_connections(self):
        for board in self.plan['anchors']+self.plan['tags']:
            session = self.connections.get(board['name'])
            if session is not None and not session.closed and not session.desynchronized and session.client.is_connected:
                continue
            self.previous_survey = None
            self.emit('auto_reconnecting', f"Reconnecting {board['name']} and restoring its tracking role")
            if session is not None: await session.close()
            self.connections.pop(board['name'], None)
            await self.configure(board, board in self.plan['tags'])

    async def poll(self):
        reconnect_failures = 0
        while not self.stopping:
            try:
                await self.cycle()
                reconnect_failures = 0
            except asyncio.CancelledError: raise
            except Exception as exc:
                broken = any(s.closed or s.desynchronized or not s.client.is_connected for s in self.connections.values())
                broken = broken or len(self.connections) < len(self.plan['anchors'])+len(self.plan['tags'])
                if broken and reconnect_failures < 2:
                    reconnect_failures += 1
                    self.previous_survey = None
                    self.emit('auto_reconnecting', f'BLE interrupted; restoring tracking connections ({reconnect_failures}/2)')
                    await asyncio.sleep(1)
                else:
                    self.emit('multi_failed',str(exc)); return

    async def configure(self, board, is_tag):
        await super().configure(board, is_tag)
        if not is_tag:
            session = self.connections[board['name']]
            await session.command('uwb anchors ' + ' '.join(a['id'] for a in self.plan['anchors'] if a != board))
            await session.command('uwb listen')

    async def measure(self, source, target):
        session = self.connections[source['name']]
        for attempt in range(3):
            try:
                lines = await session.command(f"uwb range {target['id']}")
            except RuntimeError as exc:
                match = re.search(r'board returned rc=(-?\d+);', str(exc))
                if not match or int(match[1]) not in RETRYABLE_RANGE_CODES: raise
                if attempt < 2: await asyncio.sleep(.025)
            else:
                values = [p for line in lines if (p := parse_uwb(line)) and p[0] == target['id']]
                if len(values) != 1: raise RuntimeError('Missing range acknowledgement')
                return values[0][1], time.monotonic()
        return None, time.monotonic()

    async def cycle(self, samples=1):
        await self.ensure_connections()
        anchors = self.plan['anchors']
        pairs = list(itertools.combinations(anchors, 2))
        # The closing survey also opens the next cycle: six fewer exchanges.
        # Alternate directions so consecutive surveys remain reciprocal.
        if self.previous_survey is None:
            start = time.monotonic()
            self.emit('auto_begin', start)
            self.survey_reverse = False
            before = [await self.measure(a,b) for a,b in pairs]
        else:
            before, start = self.previous_survey
            self.emit('auto_begin', start)
        tags = []
        for tag in self.plan['tags']:
            tag_rows = []
            for anchor in anchors:
                distance, stamp = await self.measure(anchor, tag)
                tag_rows.append([anchor['id'], tag['id'], distance, stamp])
            tags.extend(tag_rows)
            self.emit('auto_tag', tag_rows)
        # Reciprocal repeat adds a consistency check; it cannot prove LOS.
        self.survey_reverse = not self.survey_reverse
        survey_start = time.monotonic()
        after = [await self.measure(b,a) if self.survey_reverse else await self.measure(a,b) for a,b in pairs]
        self.previous_survey = (after, survey_start)
        self.emit('auto_epoch', dict(start=start, end=time.monotonic(), before=before, after=after, tags=tags))


class AutoAnchorEstimate(TrackingEstimate):
    automatic = True

    def __init__(self, plan, clock=time.monotonic, mode='xyz'):
        super().__init__(plan, clock)
        self.mode = mode
        self.reason = 'Waiting for an automatic anchor survey'
        self.last_epoch = None
        self.motion_during_epoch = False
        self.results = {}
        self.edge_values = []
        self.last_display = None
        self.motion_times = deque(maxlen=100)

    def begin(self, start=None):
        self.motion_during_epoch = start is not None and any(t>=start for t in self.motion_times)

    def feed(self, board, line, received):
        matched = super().feed(board,line,received)
        if matched and board in {a['name'] for a in self.plan['anchors']}:
            m = self.motion[board]
            if m.last is not None and (m.acc > .5 or m.gyro > .15):
                self.motion_during_epoch = True
                self.motion_times.append(received)
                self.invalidate('Anchor motion detected; waiting for a settled survey')
        return matched

    def invalidate(self, reason):
        self.reason = reason
        self.profile = None
        self.results = {}
        self.ranges = {}

    def accept(self, epoch):
        self.invalidate('Checking anchor survey')
        self.last_epoch = epoch
        self.edge_values = []
        try:
            if epoch['end']-epoch['start'] > MAX_EPOCH_SECONDS:
                raise ValueError('Survey took over 5 seconds; reduce radio losses or tag count')
            if self.motion_during_epoch:
                raise ValueError('Anchor moved during survey; waiting for the next settled survey')
            for before,after in zip(epoch['before'],epoch['after']):
                if before[0] is None or after[0] is None:
                    raise ValueError('Incomplete anchor survey; retrying automatically')
                if abs(before[0]-after[0]) > MAX_EDGE_CHANGE_M:
                    raise ValueError('Anchor distances changed by > 5 cm or disagree; resurveying')
                self.edge_values.append((before[0]+after[0])/2)
            coords = relative_layout(self.edge_values, self.mode)
            self.profile = dict(mode=self.mode, tag_height_m=0. if self.mode=='xy' else None,
                                anchors={a['name']:p for a,p in zip(self.plan['anchors'],coords)})
            self.ranges = {(a,t):(d,stamp) for a,t,d,stamp in epoch['tags'] if d is not None}
            for tag in self.plan['tags']:
                try:
                    values = [self.ranges[a['id'],tag['id']] for a in self.plan['anchors']]
                    if any(v[0] <= 0 for v in values): raise ValueError('Invalid tag distance')
                    if max(v[1] for v in values)-min(v[1] for v in values)>1.0:
                        raise ValueError('Tag measurements span over 1 second')
                    self.results[tag['id']] = position(coords, [v[0] for v in values],self.mode,0. if self.mode=='xy' else None)
                except (KeyError, ValueError): pass
            self.reason = ('Relative 2D survey accepted — all boards assumed in the same plane' if self.mode=='xy' else
                           'Relative 3D survey accepted — axes follow anchors; Z is not gravity height')
            # Hold a coherent frame: never place old tag coordinates into a new
            # anchor layout. Once a snapshot exists, replace it only when all
            # selected tags have a solution in the new frame.
            if self.results and (self.last_display is None or len(self.results)==len(self.plan['tags'])):
                results = dict(self.results)
                def locate(tag):
                    if tag not in results: raise ValueError('Waiting for first valid tag position')
                    return results[tag]
                self.last_display = SimpleNamespace(automatic=True,plan=self.plan,profile=self.profile,
                    ranges=dict(self.ranges),results=results,locate=locate,stamp=epoch['start'])
        except ValueError as exc: self.invalidate(str(exc))

    def display(self):
        """Presentation-only history; locate() still rejects invalid live data."""
        self.expire()
        if self.last_display is None: return self
        self.last_display.held = self.profile is not self.last_display.profile
        self.last_display.age = max(0.,self.clock()-self.last_display.stamp)
        return self.last_display

    def update_tag(self, rows):
        """Early tag fix against a still-fresh accepted anchor survey.

        Does not extend anchor survey validity. A failed closing survey will
        mark this display held; motion evidence invalidates it immediately.
        """
        self.expire()
        if self.profile is None or self.motion_during_epoch or self.last_display is None: return False
        if self.last_display.profile is not self.profile: return False
        if len(rows)!=4 or len({r[1] for r in rows})!=1: return False
        tag=rows[0][1]
        if tag not in {t['id'] for t in self.plan['tags']}: return False
        values={r[0]:r for r in rows}
        if set(values)!={a['id'] for a in self.plan['anchors']}: return False
        stamps=[r[3] for r in rows]
        if max(stamps)-min(stamps)>1. or self.clock()-min(stamps)>1.5: return False
        if any(r[2] is None or not math.isfinite(r[2]) or r[2]<=0 for r in rows): return False
        try:
            result=position([self.profile['anchors'][a['name']] for a in self.plan['anchors']],
                            [values[a['id']][2] for a in self.plan['anchors']],self.mode,0. if self.mode=='xy' else None)
        except ValueError: return False
        self.results[tag]=result
        self.last_display.results[tag]=result
        for a,t,d,s in rows:
            self.ranges[a,t]=(d,s); self.last_display.ranges[a,t]=(d,s)
        return True

    def expire(self):
        if self.last_epoch and self.clock()-self.last_epoch['start'] > MAX_EPOCH_SECONDS:
            self.invalidate('Survey stale; waiting for a complete fresh measurement cycle')

    def locate(self, tag):
        self.expire()
        if self.profile is None: raise ValueError(self.reason)
        if tag not in self.results: raise ValueError('Tag ranges incomplete or inconsistent with this survey')
        return self.results[tag]
