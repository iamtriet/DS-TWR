import sys
from pathlib import Path
import math
import unittest
import tempfile
import json
import csv
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from tracking_estimation import Motion, RangeFilter, position, TrackingEstimate, validate_profile
from multi_anchor import make_plan
from check_range_accuracy import evaluate


class EstimationTests(unittest.TestCase):
    def test_xyz_and_fixed_height_xy(self):
        anchors = [[0,0,0],[4,0,0],[0,4,0],[0,0,3]]
        target = [1.2,.8,1.1]
        ranges = [math.dist(a,target) for a in anchors]
        xyz,residual = position(anchors,ranges)
        self.assertLess(math.dist(xyz,target),1e-8)
        self.assertLess(residual,1e-8)
        plane = [[0,0,0],[4,0,0],[0,4,0],[4,4,0]]
        ranges = [math.dist(a,target) for a in plane]
        with self.assertRaises(ValueError): position(plane,ranges)
        xy,_ = position(plane,ranges,'xy',1.1)
        self.assertLess(math.dist(xy,target),1e-8)

    def test_motion_quality_staleness_and_invalid_data(self):
        m = Motion()
        for i in range(5): self.assertTrue(m.feed(f'MOTION,{i},1,3,0.03,0.01',i*.2))
        self.assertEqual(m.state(.9),'QUIET')
        self.assertEqual(m.state(2),'NO IMU')
        self.assertFalse(m.feed('MOTION,5,1,3,nan,0',2))
        m.feed('MOTION,6,1,0,0.01,0.01',2)
        self.assertEqual(m.state(2),'CALIBRATING')
        m.feed('MOTION,7,1,3,0.7,0.2',2.1)
        self.assertTrue(m.moved)
        self.assertEqual(m.state(2.1),'MOVING')

    def test_smoothing_never_hides_motion_or_large_innovation(self):
        f = RangeFilter()
        self.assertEqual(f.update(1,0,True),1)
        self.assertLess(f.update(1.02,.1,True),1.02)
        self.assertEqual(f.update(1.5,.2,True),1.5)
        self.assertEqual(f.update(1.52,.3,False),1.52)
        self.assertEqual(f.update(1.53,2,True),1.53)

    def test_missing_geometry_stale_ranges_and_anchor_motion(self):
        boards = [dict(name=f'PCB_{i:08X}',address=str(i)) for i in range(5)]
        plan = make_plan(boards,[b['name'] for b in boards[:4]])
        now = [0.]
        model = TrackingEstimate(plan,lambda:now[0])
        with self.assertRaisesRegex(ValueError,'coordinates'): model.locate('B001')
        anchors = [[0,0,0],[4,0,0],[0,4,0],[0,0,3]]
        profile = dict(mode='xyz',anchors={b['name']:a for b,a in zip(boards,anchors)})
        model.profile = validate_profile(profile,plan)
        target = [1,1,1]
        for b,a in zip(plan['anchors'],anchors): model.update(b['id'],'B001',math.dist(a,target))
        self.assertLess(math.dist(model.locate('B001')[0],target),1e-8)
        now[0]=2
        with self.assertRaisesRegex(ValueError,'stale'): model.locate('B001')
        now[0]=0
        model.feed(boards[0]['name'],'MOTION,1,1,3,0.9,0.3',0)
        with self.assertRaisesRegex(ValueError,'motion'): model.locate('B001')

    def test_reference_report_preserves_signed_bias_and_position_error(self):
        boards = [dict(name=f'PCB_{i:08X}',address=str(i)) for i in range(5)]
        plan = make_plan(boards,[b['name'] for b in boards[:4]])
        anchors = [[0,0,0],[4,0,0],[0,4,0],[0,0,3]]
        target = [1,1,1]
        profile = dict(mode='xyz',anchors={b['name']:a for b,a in zip(boards,anchors)},
                       reference_tags={boards[4]['name']:target},range_bias_m={})
        for b in boards[:4]: profile['range_bias_m'][b['name']+'/'+boards[4]['name']] = .1
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'four-anchor-plan.json').write_text(json.dumps(plan))
            with (path/'ranges.csv').open('w',newline='') as out:
                writer = csv.writer(out)
                writer.writerow(['host_utc','board','tag_hex','distance_m','age_ms'])
                for b,a in zip(boards,anchors):
                    writer.writerow(['2026-10-08T00:00:00+00:00',b['name'],'B001',math.dist(a,target)+.1,0])
            report = evaluate(path,profile)
            self.assertLess(report['position_errors'][boards[4]['name']]['rmse_m'],1e-7)
            self.assertTrue(all(v['fraction_within_3cm']==1 for v in report['links'].values()))
            profile['range_bias_m'] = {}
            report = evaluate(path,profile)
            self.assertTrue(all(abs(v['mean_error_m']-.1)<1e-8 for v in report['links'].values()))
