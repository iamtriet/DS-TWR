"""Compare recorded RAW UWB ranges with surveyed, stationary reference positions.

This assesses range error and position error when valid geometry is supplied. Never calibrate and validate
against the same capture. Board coordinates and reference tags are in metres.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics
from datetime import datetime
from tracking_estimation import TrackingEstimate, validate_profile


def evaluate(session, survey):
    plan = json.loads((session / 'four-anchor-plan.json').read_text(encoding='utf-8'))
    anchors = survey['anchors']
    references = survey['reference_tags']
    tags = {t['id']: t['name'] for t in plan['tags']}
    allowed_anchors = {a['name'] for a in plan['anchors']}
    for name in allowed_anchors | set(tags.values()):
        xyz = anchors.get(name) if name in allowed_anchors else references.get(name)
        if not isinstance(xyz,list) or len(xyz)!=3 or not all(isinstance(v,(float,int)) and math.isfinite(v) for v in xyz):
            raise ValueError(f'Missing measured XYZ reference for {name}')
    errors, position_errors = {}, {}
    now = [0.0]
    estimator = TrackingEstimate(plan,lambda:now[0])
    geometry_error = None
    try: estimator.profile = validate_profile(survey,plan)
    except ValueError as exc: geometry_error = str(exc)
    anchor_ids = {a['name']: a['id'] for a in plan['anchors']}
    with (session / 'ranges.csv').open(newline='',encoding='utf-8') as source:
        for row in csv.DictReader(source):
            if row['board'] not in allowed_anchors or row['tag_hex'] not in tags: continue
            tag = tags[row['tag_hex']]
            key = row['board']+'/'+tag
            measured = float(row['distance_m'])
            if not math.isfinite(measured): raise ValueError('Nonfinite measured range')
            expected = math.dist(anchors[row['board']],references[tag])
            corrected = measured-survey.get('range_bias_m',{}).get(key,0)
            errors.setdefault(key,[]).append(corrected-expected)
            now[0] = datetime.fromisoformat(row['host_utc']).timestamp()
            estimator.update(anchor_ids[row['board']],row['tag_hex'],measured)
            if estimator.profile:
                try: xyz,_ = estimator.locate(row['tag_hex'])
                except ValueError: continue
                position_errors.setdefault(tag,[]).append(math.dist(xyz,references[tag]))
    report = {}
    for a in allowed_anchors:
        for tag in tags.values():
            key = a+'/'+tag
            values = errors.get(key,[])
            if not values: raise ValueError(f'No samples for {key}')
            absolute = sorted(abs(v) for v in values)
            report[key] = dict(samples=len(values),mean_error_m=statistics.mean(values),
                rmse_m=math.sqrt(statistics.mean(v*v for v in values)),
                p95_absolute_error_m=absolute[math.ceil(.95*len(absolute))-1],
                max_absolute_error_m=max(absolute),
                fraction_within_3cm=sum(v<.03 for v in absolute)/len(absolute))
    positions = {tag:dict(samples=len(values),rmse_m=math.sqrt(statistics.mean(v*v for v in values)),
                         p95_error_m=sorted(values)[math.ceil(.95*len(values))-1],
                         max_error_m=max(values),fraction_within_3cm=sum(v<.03 for v in values)/len(values))
                 for tag,values in position_errors.items()}
    return dict(measure='errors against supplied stationary references; reference accuracy is not verified',
                links=report,position_errors=positions,geometry_error=geometry_error)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=Path,required=True)
    parser.add_argument('--survey',type=Path,required=True)
    args=parser.parse_args()
    result = evaluate(args.session,json.loads(args.survey.read_text(encoding='utf-8')))
    out = args.session/'reference-range-errors.json'
    out.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
