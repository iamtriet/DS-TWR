import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from reference_gateway import ReferenceFleet

class ReferenceTests(unittest.IsolatedAsyncioTestCase):
    def factory(self,capable=True):
        calls=[]
        class Fake:
            def __init__(self,label,emit,address):self.label=label
            async def open(self):pass
            async def close(self):pass
            async def command(self,c):
                calls.append((self.label,c))
                if c=='uwb capabilities':return [f'reference_tdma={int(capable)}']
                if c=='info':return [f'FICR ID={int(self.label):016x}']
                return []
        return calls,Fake
    async def test_preflight_roster_keys_roles_and_coordinator_last(self):
        calls,factory=self.factory();boards=[dict(name=str(n),address=str(n)) for n in range(1,7)]
        fleet=ReferenceFleet(boards,lambda *a:None,tag=2,anchor_mask=0x1d,factory=factory)
        await fleet.configure()
        self.assertEqual([n for n,c in calls if c=='ref start experimental'],['2','3','4','5','6','1'])
        keys=[c for _,c in calls if c.startswith('ref keys ')]
        self.assertEqual(len(set(keys)),1);self.assertEqual(len(keys),6)
        self.assertTrue(all(len(part)==32 and int(part,16)>0 for part in keys[0].split()[2:]))
        self.assertEqual(len([c for n,c in calls if n=='1' and c.startswith('ref peer ')]),6)
        self.assertIn(('1','ref roles 2 1D'),calls)
        old=keys[0];await fleet.stop();calls.clear();await fleet.configure()
        self.assertNotEqual(old,next(c for _,c in calls if c.startswith('ref keys ')))
        calls.clear();await fleet.stop();self.assertEqual([n for n,c in calls if c=='uwb stop'],[str(n) for n in range(1,7)])
    async def test_wrong_image_never_provisions_or_starts(self):
        calls,factory=self.factory(False)
        fleet=ReferenceFleet([dict(name=str(n),address=str(n)) for n in (1,2)],lambda *a:None,factory=factory)
        with self.assertRaisesRegex(RuntimeError,'reference.signed.bin'):await fleet.configure()
        self.assertFalse(any(c.startswith('ref ') for _,c in calls))
        await fleet.stop()
    def test_invalid_capacity_roles_period(self):
        boards=[dict(name=str(n),address=str(n)) for n in range(1,11)]
        for selected,options in [(boards,{}),(boards[:6],dict(period_ms=100)),(boards[:6],dict(tag=2,anchor_mask=2)),(boards[:6],dict(tag=0,anchor_mask=1))]:
            with self.assertRaises(ValueError):ReferenceFleet(selected,lambda *a:None,**options)
