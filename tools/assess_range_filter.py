"""Causal replay of unique recorded link samples; measures jitter, not accuracy."""
import json,sys
from pathlib import Path
import numpy as np
from uwb3d_core import AdaptiveRangeFilter

path=Path(sys.argv[1]);series={};seen={};filters={}
with path.open() as stream:
    for line in stream:
        try:r=json.loads(line)
        except json.JSONDecodeError:continue # writer may have an incomplete last line
        if r.get('type')!='state':continue
        for e in r['state']['edges']:
            key=tuple(sorted((e['a'],e['b'])))
            if seen.get(key)==e['sequence']:continue
            seen[key]=e['sequence'];raw=e.get('raw_distance') or e['distance']
            value=filters.setdefault(key,AdaptiveRangeFilter()).update(raw,e['effective'])
            if value is not None:series.setdefault(key,[]).append((raw,value))
rows=[]
for key,values in sorted(series.items()):
    a=np.array(values)[20:]
    if len(a)<20:continue
    raw,filtered=np.std(a,axis=0)
    rows.append(dict(pair=key,samples=len(a),raw_std_m=raw,filtered_std_m=filtered,reduction_percent=100*(1-filtered/raw) if raw else 0))
report=dict(note='Same recorded samples, causal replay; standard deviation around each link mean is jitter, not absolute accuracy.',pairs=rows,
            median_reduction_percent=float(np.median([r['reduction_percent'] for r in rows])))
out=path.parent/'filter-assessment.json';out.write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
