import sys,unittest,asyncio
from pathlib import Path
from unittest.mock import AsyncMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from swarm_gateway import SwarmFleet

class ManualLEDTests(unittest.IsolatedAsyncioTestCase):
 async def test_selected_only_uid_and_ack(self):
  fleet=SwarmFleet([dict(name=str(i)) for i in range(6)],lambda *a:None,origin=6)
  fleet.identities={1:'0000000000000001',2:'0000000000000002'}
  calls=[]
  class Session:
   async def command(self,cmd):
    calls.append((self.n,cmd))
    if cmd=='info':return [f'FICR ID={self.n:016x}']
    return ['AUX_LED v=1 profile=uv UV_L=0 UV_R=0']
   async def close(self):pass
  async def opened(board):
   s=Session();s.n=int(board['name']);return s
  fleet.open_session=opened
  result=await fleet.manual_led([2],'led aux off')
  self.assertTrue(all(n==2 for n,c in calls));self.assertIn('LED_ACK',result[0])
  fleet.identities[2]='bad';calls.clear()
  result=await fleet.manual_led([2],'led aux uv 1 1')
  self.assertIn('UID mismatch',result[0]);self.assertNotIn((2,'led aux uv 1 1'),calls)
 async def test_colour_permutation_and_readback(self):
  from led_protocol import wire_command,logical_status
  self.assertEqual(wire_command('led aux rgb 1 0 0 1 0 0'),'led aux rgb 0 1 0 0 1 0')
  self.assertEqual(wire_command('led aux rgb 0 1 0 0 1 0'),'led aux rgb 0 0 1 0 0 1')
  self.assertEqual(wire_command('led aux rgb 0 0 0 0 0 1'),'led aux rgb 0 0 0 1 0 0')
  self.assertEqual(wire_command('led aux uv 1 0'),'led aux uv 1 0')
  line=logical_status('RGB_R1=unsupported RGB_G1=1 RGB_B1=0 RGB_R2=0 RGB_G2=1 RGB_B2=0')
  self.assertEqual(line,'RGB_R1=1 RGB_G1=0 RGB_B1=unsupported RGB_R2=1 RGB_G2=0 RGB_B2=0')
 async def test_warm_reuse_parallel_and_cleanup(self):
  fleet=SwarmFleet([dict(name=str(i)) for i in range(6)],lambda *a:None,origin=6)
  fleet.identities={1:'0000000000000001',2:'0000000000000002'}
  calls=[];opened=[];closed=[];active=0;peak=0
  class Session:
   async def command(self,cmd):
    calls.append((self.n,cmd));await asyncio.sleep(.001)
    if cmd=='info':return [f'FICR ID={self.n:016x}']
    return ['AUX_LED v=1 profile=off UV_L=0 UV_R=0']
   async def close(self):closed.append(self.n)
  async def opening(board):
   nonlocal active,peak
   active+=1;peak=max(peak,active);await asyncio.sleep(.01);active-=1
   session=Session();session.n=int(board['name']);opened.append(session.n);return session
  fleet.open_session=opening
  await fleet.manual_led([1,2],'led aux off');calls.clear()
  rows=await fleet.manual_led([1,2],'led aux off')
  self.assertEqual(peak,2);self.assertEqual(opened,[1,2]);self.assertFalse(closed)
  self.assertEqual(sorted(calls),[(1,'led aux off'),(2,'led aux off')])
  self.assertTrue(all('connection=warm' in r for r in rows))
  await fleet.close_led_sessions();self.assertEqual(sorted(closed),[1,2]);self.assertFalse(fleet.led_sessions)
 async def test_multi_node_picking_ignores_trail_overlays(self):
  from uwb3d_view import Scene
  from types import SimpleNamespace
  scene=SimpleNamespace(state={'positions':{1:[100,100,0],2:[200,200,0]}},selected=None,selected_nodes=set(),follow=False,project=lambda p,c:p[:2],render=lambda s:None)
  for x,y in [(100,100),(200,200)]:Scene.press(scene,SimpleNamespace(x=x,y=y))
  self.assertEqual(scene.selected_nodes,{1,2})
  Scene.press(scene,SimpleNamespace(x=100,y=100));self.assertEqual(scene.selected_nodes,{2})
 async def test_reject_non_led_commands(self):
  fleet=SwarmFleet([{}]*6,lambda *a:None,origin=6)
  with self.assertRaises(ValueError):await fleet.manual_led([1],'uwb stop')
  with self.assertRaises(ValueError):await fleet.manual_led([1],'led aux rgb 0 0 1 0 0 0')
 async def test_led_operation_preserves_tracking(self):
  from control_app import ControlApp
  from types import SimpleNamespace
  from unittest.mock import patch
  runtime=SimpleNamespace(gateway=object(),stop=AsyncMock())
  app=SimpleNamespace(busy=False,closing=False,status=SimpleNamespace(set=lambda x:None),uwb3d=SimpleNamespace(runtime=runtime),loop=asyncio.get_running_loop(),emit=lambda *a:None)
  tasks=[]
  with patch('control_app.asyncio.run_coroutine_threadsafe',side_effect=lambda coro,loop:tasks.append(asyncio.create_task(coro))):
   ControlApp.run(app,'LED',AsyncMock(return_value=[]),preserve_tracking=True)
  await asyncio.gather(*tasks);runtime.stop.assert_not_awaited()
if __name__=='__main__':unittest.main()
