import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from swarm_gateway import SwarmFleet


class CollectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_collector_uses_last_id_and_recovery_preserves_role(self):
        boards=[dict(name=f'board-{i}') for i in range(6)]
        fleet=SwarmFleet(boards,lambda *args:None,origin=6)
        commands=[]
        class Session:
            def __init__(self,board):self.index=int(board['name'].split('-')[-1])
            async def command(self,command):
                commands.append((self.index,command))
                if command=='info':return [f'FICR ID={self.index+1:016x}', 'PCB_VERSION=1.2.6']
                if command=='uwb capabilities':return ['SWARM broadcast_DS_TWR=1']
                if command=='swarm status':return ['SWARM running=1']
                if command=='uwb status':return ['UWB ID=123 ready=1']
                return []
            async def close(self):pass
        async def session(board):return Session(board)
        fleet.open_session=session
        await fleet.configure()
        self.assertEqual(set(fleet.identities),set(range(1,7)))
        self.assertEqual(fleet.identities[6],'0000000000000001')
        self.assertIn((0,'swarm configure 6 6 53574131'),commands)
        self.assertIn((0,'swarm collector 1'),commands)
        for i in range(1,6):self.assertIn((i,f'swarm configure {i} 6 53574131'),commands)
        commands.clear()
        self.assertFalse(await fleet.recover(6))
        self.assertNotIn((0,'uwb stop'),commands)
        commands.clear()
        self.assertTrue(await fleet.recover(6,restart_running=True))
        self.assertIn((0,'uwb stop'),commands)
        self.assertIn((0,'swarm collector 1'),commands)
        self.assertIn((0,'swarm configure 6 6 53574131'),commands)

if __name__=='__main__':unittest.main()
