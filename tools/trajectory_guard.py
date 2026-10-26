"""Mission-only motion plausibility checks; never modifies the UWB solver."""
from collections import deque
import math


class TrajectoryGuard:
    def __init__(self):
        self.history = {}
        self.quiet = {}
        self.candidates = {}

    def gap(self, node):
        self.history.pop(node, None)
        self.quiet.pop(node, None)
        self.candidates.pop(node, None)

    def check(self, node, t, p, sigma, moving, max_speed):
        history = self.history.setdefault(node, deque())
        if history and t <= history[-1][0]:
            return 'DUPLICATE', 0.
        # Compare against a time window, not sigma / a tiny frame interval.
        while len(history) > 1 and t-history[1][0] > 1.:
            history.popleft()
        speed = 0.
        reason = None
        for old_t, old_p, old_sigma in history:
            dt = t-old_t
            lower = max(0., math.dist(p, old_p)-2*math.hypot(sigma, old_sigma))
            speed = max(speed, lower/max(dt, .001))
            if lower > max_speed*min(dt, 1.):
                reason = 'SPEED EXCEEDED'
        if moving is False:
            anchor = self.quiet.setdefault(node, (t, list(p), sigma))
            if t-anchor[0] >= .5 and math.dist(p, anchor[1]) > max(.08, 3*math.hypot(sigma, anchor[2])):
                reason = 'DRIFT SUSPECTED / IMU QUIET'
        else:
            self.quiet.pop(node, None)
        if reason:
            # A rejected point never becomes the next trusted baseline.
            # Reacquire only a coherent moving trajectory lasting >=1 s.
            candidate = self.candidates.get(node)
            if moving is True:
                if candidate is None or t-candidate[1] > .5 or math.dist(p,candidate[2]) > max_speed*(t-candidate[1])+2*math.hypot(sigma,candidate[3]):
                    candidate = (t,t,list(p),sigma,list(p),sigma)
                else:
                    if math.dist(p,candidate[4]) > max_speed*(t-candidate[0])+2*math.hypot(sigma,candidate[5]):
                        candidate = (t,t,list(p),sigma,list(p),sigma)
                    else:
                        candidate = (candidate[0],t,list(p),sigma,candidate[4],candidate[5])
                self.candidates[node] = candidate
                if t-candidate[0] >= 1.:
                    history.clear();history.append((t,list(p),sigma));self.candidates.pop(node,None)
                    return 'REACQUIRING / PATH GAP', speed
            else:
                self.candidates.pop(node,None)
            return reason, speed
        self.candidates.pop(node,None)
        history.append((t,list(p),sigma))
        return 'ACCEPTED', speed
