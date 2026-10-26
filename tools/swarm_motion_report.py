"""Summarize a recorded motion trial without treating filter smoothness as accuracy."""
import argparse
from collections import Counter, deque
import json
from pathlib import Path

import numpy as np


def summarize(path, node=2, seconds=60.):
    rows = deque()
    with Path(path).open(encoding='utf-8') as stream:
        for line in stream:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # A live writer may have an incomplete final line.
            if record.get('type') != 'state':
                continue
            state = record['state']
            rows.append(state)
            while rows and state['time'] - rows[0]['time'] > seconds:
                rows.popleft()
    if not rows:
        raise ValueError('No recorded states')
    key = str(node)
    phases = {}
    previous = None
    steps = []
    for state in rows:
        entry = next((n for n in state['nodes'] if n['id'] == node), {})
        imu = entry.get('imu') or {}
        fresh = (0 <= state['time'] - imu.get('received', -1e9) <= .25
                 and imu.get('sample_age_ms', 1000) <= 100)
        phase = ('moving' if imu.get('moving') else
                 'settling' if imu.get('quiet_ms', 0) < 1000 else 'quiet') if fresh else 'sensor-unavailable'
        bucket = phases.setdefault(phase, {'samples': 0, 'available': 0, 'delta': []})
        bucket['samples'] += 1
        xy = state['positions'].get(key)
        valid = state['node_states'].get(key) in ('estimated', 'measured-supported')
        raw = state['measured_positions'].get(key)
        if xy is not None and valid:
            bucket['available'] += 1
            if raw is not None:
                bucket['delta'].append(float(np.linalg.norm(np.array(xy[:2]) - raw[:2])))
        if xy is not None and previous is not None:
            steps.append(float(np.linalg.norm(np.array(xy[:2]) - previous)))
        previous = np.array(xy[:2]) if xy is not None else None
    result = {'node': node, 'duration_s': rows[-1]['time'] - rows[0]['time'],
              'states': dict(Counter(s['state'] for s in rows)), 'phases': {},
              'max_display_step_m': max(steps, default=None),
              'note': 'Raw/display difference is filter discrepancy, not position error. Quiet acceleration does not prove rest. No ground truth.'}
    for phase, bucket in phases.items():
        result['phases'][phase] = {
            'samples': bucket['samples'], 'available_fraction': bucket['available'] / bucket['samples'],
            'raw_display_delta_median_m': float(np.median(bucket['delta'])) if bucket['delta'] else None,
            'raw_display_delta_p95_m': float(np.percentile(bucket['delta'], 95)) if bucket['delta'] else None,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session', type=Path)
    parser.add_argument('--node', type=int, default=2)
    parser.add_argument('--seconds', type=float, default=60.)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.seconds <= 0:
        parser.error('--seconds must be positive')
    text = json.dumps(summarize(args.session, args.node, args.seconds), indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
