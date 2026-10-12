"""Explicit hardware gateway experiment; never flashes boards or actuates LEDs."""
import argparse
import asyncio
import json
import time
from pathlib import Path
from uwb3d_core import Config
from uwb3d_runtime import Runtime
from control_backend import Session


async def run(a):
    boards=json.loads(a.roster.read_text())['boards']
    boards=sorted(boards,key=lambda b:(b['name']!=a.gateway,b['name']))
    if boards[0]['name']!=a.gateway:raise ValueError('Gateway not in roster')
    a.output.mkdir(parents=True,exist_ok=True)
    runtime=Runtime(asyncio.get_running_loop(),Config(max_age=1.5,gateway_superframe_ms=a.period))
    states=[];status=[]
    try:
        await runtime.start('gateway',boards=boards,port=a.port)
        await runtime.recording(a.output/'session.jsonl')
        start=time.monotonic();active=None;last=0
        while time.monotonic()-start<a.seconds+180:
            await asyncio.sleep(.2);state=runtime.snapshot()
            if not state:continue
            if state.get('network') and active is None:active=time.monotonic()
            if time.monotonic()-last>2:
                last=time.monotonic();states.append(state)
                print(json.dumps(dict(elapsed=round(last-start,1),nodes=len(state['ids']),links=state['metrics']['valid_edges'],rate=state['metrics']['measurement_hz'],network=state['network'],error=state['error'],reason=state['reason'])),flush=True)
            if active is not None and time.monotonic()-active>=a.seconds:break
            if runtime.tasks[0].done():raise RuntimeError(runtime.error)
        if active is None:raise RuntimeError('No coordinator network telemetry')
    finally:
        try:await runtime.stop()
        finally:
            (a.output/'states.json').write_text(json.dumps(states,indent=2))
        for board in boards:
            s=Session(board['name'],lambda *args:None,address=board['address']);row=dict(board=board)
            try:
                await s.open();row['network']=await s.command('net status');row['radio']=await s.command('uwb status')
            except Exception as e:row['error']=str(e)
            finally:await s.close()
            status.append(row);print(json.dumps(row),flush=True)
        (a.output/'status.json').write_text(json.dumps(status,indent=2))
        await runtime.shutdown()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--roster',type=Path,required=True);p.add_argument('--gateway',required=True);p.add_argument('--port',required=True)
    p.add_argument('--seconds',type=float,default=30);p.add_argument('--period',type=int,default=500)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    asyncio.run(run(a))
