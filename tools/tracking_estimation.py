"""Measured-range conditioning and surveyed-anchor positioning.

IMU motion evidence gates smoothing; it is not an absolute position source.
No coordinate frame or antenna calibration is invented.
"""
from collections import deque
import math
import time


class Motion:
    def __init__(self):
        self.last = None
        self.quiet = 0
        self.quality = 0
        self.acc = self.gyro = None
        self.moved = False

    def feed(self, line, now):
        parts = line.split(',')
        if len(parts) != 6 or parts[0] != 'MOTION': return False
        try:
            timestamp, fresh, quality = map(int, parts[1:4])
            acc, gyro = map(float, parts[4:])
            if timestamp < 0 or fresh not in (0, 1) or not 0 <= quality <= 3: return False
            if not all(math.isfinite(v) and v >= 0 for v in (acc, gyro)): return False
        except ValueError: return False
        self.last = now if fresh else None
        self.quality, self.acc, self.gyro = quality, acc, gyro
        quiet = fresh and quality >= 2 and acc < .12 and gyro < .04
        self.quiet = self.quiet+1 if quiet else 0
        if fresh and (acc > .5 or gyro > .15): self.moved = True
        return True

    def state(self, now):
        if self.last is None or now-self.last > .75: return 'NO IMU'
        if self.quality < 2: return 'CALIBRATING'
        return 'QUIET' if self.quiet >= 5 else 'MOVING'


class RangeFilter:
    def __init__(self):
        self.value = self.last = None
        self.raw = deque(maxlen=12)
        self.active = False

    def update(self, distance, now, quiet):
        if not math.isfinite(distance) or distance < 0: raise ValueError('Invalid measured distance')
        self.raw.append(distance)
        dt = 0 if self.last is None else now-self.last
        # Never smooth motion evidence, large innovations, or gaps. Range change
        # matters because an IMU cannot distinguish rest from constant velocity.
        smooth = quiet and self.value is not None and 0 < dt < .75 and abs(distance-self.value) < .04
        self.active = smooth
        gain = 1-math.exp(-dt/.15) if smooth else 1.0
        self.value = distance if self.value is None else self.value+gain*(distance-self.value)
        self.last = now
        return self.value


def solve(matrix, vector):
    """Small pivoted solve; reject degenerate/ill-conditioned geometry."""
    n = len(vector)
    rows = [list(row)+[v] for row, v in zip(matrix, vector)]
    scale = max(abs(v) for row in matrix for v in row)
    if not scale: raise ValueError('Degenerate anchor geometry')
    for i in range(n):
        pivot = max(range(i, n), key=lambda j: abs(rows[j][i]))
        rows[i], rows[pivot] = rows[pivot], rows[i]
        divisor = rows[i][i]
        if abs(divisor) < scale*1e-7: raise ValueError('Anchor geometry is too flat or collinear')
        rows[i] = [v/divisor for v in rows[i]]
        for j in range(n):
            if j != i:
                factor = rows[j][i]
                rows[j] = [a-factor*b for a, b in zip(rows[j], rows[i])]
    return [row[-1] for row in rows]


def least_squares(rows, values):
    n = len(rows[0])
    return solve([[sum(r[i]*r[j] for r in rows) for j in range(n)] for i in range(n)],
                 [sum(r[i]*v for r, v in zip(rows, values)) for i in range(n)])


def position(anchors, distances, mode='xyz', height=None):
    n = 3 if mode == 'xyz' else 2
    if len(anchors) < n+1 or len(anchors) != len(distances): raise ValueError('Insufficient ranges')
    if n == 2 and (height is None or not math.isfinite(height)): raise ValueError('Supply the measured tag height')
    a0, d0 = anchors[0], distances[0]
    rows, rhs = [], []
    for a, d in zip(anchors[1:], distances[1:]):
        rows.append([2*(a[i]-a0[i]) for i in range(n)])
        v = d0*d0-d*d+sum(x*x-y*y for x,y in zip(a,a0))
        if n == 2: v -= 2*(a[2]-a0[2])*height
        rhs.append(v)
    xyz = least_squares(rows, rhs)
    if n == 2: xyz.append(height)
    for _ in range(8):
        jac, errors = [], []
        for a,d in zip(anchors, distances):
            delta = [xyz[i]-a[i] for i in range(3)]
            predicted = math.sqrt(sum(v*v for v in delta))
            if predicted < 1e-5: raise ValueError('Position coincides with anchor')
            jac.append([v/predicted for v in delta[:n]])
            errors.append(d-predicted)
        step = least_squares(jac, errors)
        for i in range(n): xyz[i] += step[i]
        if sum(v*v for v in step) < 1e-10: break
    residual = math.sqrt(sum((math.dist(xyz,a)-d)**2 for a,d in zip(anchors,distances))/len(anchors))
    if not all(math.isfinite(v) for v in xyz) or residual > .15: raise ValueError('Ranges do not fit the surveyed geometry')
    return xyz, residual


def validate_profile(profile, plan):
    if profile.get('mode') not in ('xyz', 'xy'): raise ValueError('Profile mode must be xyz or xy')
    coords = profile.get('anchors', {})
    for a in plan['anchors']:
        xyz = coords.get(a['name'])
        if not isinstance(xyz, list) or len(xyz) != 3 or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in xyz):
            raise ValueError(f"Supply measured XYZ coordinates for {a['name']}")
    if profile['mode'] == 'xy':
        z = profile.get('tag_height_m')
        if not isinstance(z,(float,int)) or not math.isfinite(z): raise ValueError('Supply tag_height_m for XY mode')
    for key, bias in profile.get('range_bias_m', {}).items():
        if not isinstance(bias,(float,int)) or not math.isfinite(bias) or abs(bias)>2:
            raise ValueError(f'Invalid measured range bias for {key}')
    # Test geometry with arbitrary distances only to exercise the linear rank;
    # this point is never displayed or used as a measurement.
    anchors = [coords[a['name']] for a in plan['anchors']]
    n = 3 if profile['mode'] == 'xyz' else 2
    least_squares([[a[i]-anchors[0][i] for i in range(n)] for a in anchors[1:]], [0]*(len(anchors)-1))
    return profile


class TrackingEstimate:
    def __init__(self, plan, clock=time.monotonic):
        self.plan, self.clock = plan, clock
        self.motion = {b['name']: Motion() for b in plan['anchors']+plan['tags']}
        self.filters, self.ranges = {}, {}
        self.profile = None

    def feed(self, board, line, received):
        if board in self.motion: return self.motion[board].feed(line, received)

    def update(self, anchor, tag, raw):
        now = self.clock()
        a = next(b for b in self.plan['anchors'] if b['id']==anchor)
        t = next(b for b in self.plan['tags'] if b['id']==tag)
        quiet = all(self.motion[b['name']].state(now)=='QUIET' for b in (a,t))
        key = (anchor, tag)
        filtered = self.filters.setdefault(key, RangeFilter()).update(raw, now, quiet)
        self.ranges[key] = (filtered, now)
        return filtered, 'IMU SMOOTHED' if self.filters[key].active else 'RAW UWB'

    def locate(self, tag):
        if self.profile is None: raise ValueError('Load surveyed anchor coordinates')
        now = self.clock()
        if any(self.motion[a['name']].moved for a in self.plan['anchors']):
            raise ValueError('Anchor motion detected: stop and resurvey anchors')
        t = next(t for t in self.plan['tags'] if t['id']==tag)
        distances, times = [], []
        for a in self.plan['anchors']:
            if (a['id'],tag) not in self.ranges: raise ValueError('Waiting for all four links')
            distance, stamp = self.ranges[a['id'],tag]
            distances.append(distance-self.profile.get('range_bias_m',{}).get(a['name']+'/'+t['name'],0))
            times.append(stamp)
        if now-min(times)>1.5 or max(times)-min(times)>1.0: raise ValueError('Ranges stale or too far apart in time')
        if min(distances)<=0: raise ValueError('Calibration produced an invalid distance')
        return position([self.profile['anchors'][a['name']] for a in self.plan['anchors']], distances,
                        self.profile['mode'], self.profile.get('tag_height_m'))
