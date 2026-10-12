import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from uwb3d_gateway import GatewayFleet


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    def factory(self,fail=None,version='1.0.18'):
        calls=[]
        class Session:
            def __init__(self,label,emit,address):self.label=label
            async def open(self):calls.append((self.label,'open'))
            async def close(self):calls.append((self.label,'close'))
            async def command(self,command):
                calls.append((self.label,command))
                if fail and fail(self.label,command):raise RuntimeError('injected failure')
                if command=='uwb capabilities':return ['experimental_sparse_BP=1']
                if command=='info':return [f'PCB_VERSION={version}',f'FICR ID={int(self.label):016X}']
                return []
        return calls,Session
    async def test_followers_first_coordinator_last_and_no_actuation(self):
        calls,factory=self.factory()
        boards=[dict(name=str(i),address=str(i)) for i in range(1,7)]
        fleet=GatewayFleet(boards,lambda *args:None,factory=factory)
        await fleet.configure()
        self.assertEqual([n for n,c in calls if c=='net start experimental'],['2','3','4','5','6','1'])
        self.assertFalse(any(c.split()[0] in ('led','ota','imu','pwm','uv') for _,c in calls))
        calls.clear();self.assertEqual(await fleet.stop(),[])
        self.assertEqual([n for n,c in calls if c=='uwb stop'],['1','2','3','4','5','6'])
        self.assertEqual(fleet.started,[])
    async def test_partial_setup_cleanup_keeps_failed_board_for_retry(self):
        failing={'setup':True,'stop':False}
        calls,factory=self.factory(lambda n,c:(failing['setup'] and n=='3' and c=='net start experimental') or (failing['stop'] and n=='2' and c=='uwb stop'))
        fleet=GatewayFleet([dict(name=str(i),address=str(i)) for i in range(1,4)],lambda *args:None,factory=factory)
        with self.assertRaises(RuntimeError):await fleet.configure()
        self.assertNotIn(('1','net start experimental'),calls)
        failing['setup']=False;failing['stop']=True
        self.assertTrue(await fleet.stop());self.assertEqual([b['name'] for b in fleet.started],['2'])
        failing['stop']=False;self.assertEqual(await fleet.stop(),[])
    def test_reject_unqualified_fast_schedule_and_duplicate_board(self):
        boards=[dict(name='1',address='1'),dict(name='2',address='2')]
        with self.assertRaises(ValueError):GatewayFleet(boards,lambda *a:None,slot_ms=3,period_ms=100)
        with self.assertRaises(ValueError):GatewayFleet([boards[0],boards[0]],lambda *a:None)
    async def test_reject_firmware_with_latched_clock_before_radio_start(self):
        calls,factory=self.factory(version='1.0.17')
        fleet=GatewayFleet([dict(name=str(i),address=str(i)) for i in (1,2)],lambda *a:None,factory=factory)
        with self.assertRaisesRegex(RuntimeError,'1.0.18'):await fleet.configure()
        self.assertFalse(any(c in ('uwb init','net start experimental') for _,c in calls))
        self.assertEqual(await fleet.stop(),[])
