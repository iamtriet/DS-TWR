"""Grid boundaries derived from observed behavior, not inferred physical walls."""
import math


class AutomaticFences:
    def __init__(self, cell=.25):
        self.cell = cell
        self.explored = set()
        self.exclusions = set()
        self.frontier = set();self.caution=set()

    def update(self, behavior_map):
        self.cell = behavior_map.cell
        rows = behavior_map.cells()
        self.explored = {tuple(r['cell']) for r in rows if r['state'] == 'OBSERVED PASSAGE'}
        # Recompute so a later traversal retracts contradicted hypotheses.
        self.exclusions = {tuple(r['cell']) for r in rows if r['state'] == 'SUSPECTED OBSTRUCTION'}
        self.explored -= self.exclusions
        known={tuple(r['cell']) for r in rows}
        self.frontier={(x+dx,y+dy) for x,y in self.explored for dx,dy in ((-1,0),(1,0),(0,-1),(0,1))}-known
        self.caution={(x+dx,y+dy) for x,y in self.exclusions for dx in (-1,0,1) for dy in (-1,0,1)}-self.exclusions

    def edges(self, cells):
        """Union boundary: preserves holes and disconnected components."""
        result = []
        for x, y in sorted(cells):
            for neighbor, a, b in (
                ((x-1,y),(x,y),(x,y+1)),
                ((x+1,y),(x+1,y),(x+1,y+1)),
                ((x,y-1),(x,y),(x+1,y)),
                ((x,y+1),(x,y+1),(x+1,y+1)),
            ):
                if neighbor not in cells:
                    result.append(([v*self.cell for v in a], [v*self.cell for v in b]))
        return result

    def state(self, p, margin):
        key = tuple(math.floor(v/self.cell) for v in p[:2])
        # Uncertainty buffer around suspected cells, not around all unknown space.
        for x, y in self.exclusions:
            dx = max(x*self.cell-p[0], 0., p[0]-(x+1)*self.cell)
            dy = max(y*self.cell-p[1], 0., p[1]-(y+1)*self.cell)
            if math.hypot(dx, dy) <= margin:
                return 'CAUTION / SUSPECTED OBSTACLE'
        if key in self.explored:
            return 'OBSERVED AREA'
        return 'UNKNOWN FRONTIER' if self.explored else 'LEARNING FENCE'

    def document(self):
        return dict(cell_size=self.cell, explored_cells=sorted(self.explored),
                    exclusion_cells=sorted(self.exclusions),frontier_cells=sorted(self.frontier),caution_cells=sorted(self.caution),
                    semantics='Observed-area frontier and tentative obstruction warnings; not physical walls')
