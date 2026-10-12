"""Summarize recorded physical ranges; precision is not absolute accuracy."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import statistics
from uwb3d_protocol import Decoder, RANGE, decode_range


def summarize(path):
    decoder=Decoder(); links=defaultdict(list); seen=set(); states=[]
    for line in path.open(encoding='utf-8'):
        row=json.loads(line)
        if row.get('type')=='state':states.append(row['state'])
        if row.get('type')!='raw':continue
        for frame in decoder.feed(bytes.fromhex(row['hex'])):
            if frame.kind!=RANGE:continue
            r=decode_range(frame.payload)
            key=(r['a'],r['b'],r['sequence'])
            if not r['valid'] or key in seen:continue
            seen.add(key);links[key[:2]].append((row['elapsed'],r['distance']))
    result=[]
    for pair,values in sorted(links.items()):
        times,distances=zip(*values);duration=times[-1]-times[0]
        result.append(dict(nodes=pair,samples=len(values),median_m=statistics.median(distances),
            stddev_m=statistics.pstdev(distances),min_m=min(distances),max_m=max(distances),
            observed_hz=(len(values)-1)/duration if duration else None,
            max_receipt_gap_s=max((b-a for a,b in zip(times,times[1:])),default=None)))
    active=[s for s in states if s.get('positions') and s.get('edges')]
    medians={}
    for key in ('localization_hz','measurement_hz','solver_ms','rms','mean_age','max_age','measurement_to_publish_ms'):
        values=[s['metrics'][key] for s in active if s['metrics'].get(key) is not None]
        if values:medians[key]=statistics.median(values)
    if active:
        medians['newest_measurement_to_publish_ms']=statistics.median(
            1000*(s['published_at']-max(e['effective'] for e in s['edges'])) for s in active)
    return dict(recording=str(path),unique_ranges=len(seen),links=result,active_state_medians=medians,
        position_states=sum(bool(s.get('measured_positions')) for s in states),
        states=len(states),last_state=states[-1] if states else None,
        note='Range spread measures repeatability, not accuracy; no surveyed ground truth supplied.')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording',type=Path);parser.add_argument('--output',type=Path)
    args=parser.parse_args();report=summarize(args.recording);text=json.dumps(report,indent=2)
    if args.output:args.output.write_text(text,encoding='utf-8')
    print(text)
