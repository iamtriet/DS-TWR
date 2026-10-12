"""Exercise a cooperative fleet and save measured evidence (no accuracy claim)."""
import argparse
import asyncio
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from cooperative_tracking import CooperativeFleet


async def run(plan, output, cycles):
    output.mkdir(parents=True, exist_ok=True)
    frames, slots = [], []
    result = dict(started_utc=datetime.now(timezone.utc).isoformat(), plan=plan,
                  mode='xy', frames=frames, slots=slots)
    with (output/'console.jsonl').open('w', encoding='utf-8') as log:
        def emit(kind, value):
            log.write(json.dumps([kind,value])+'\n'); log.flush()
            if kind=='coop_slot': slots.append(value)
            elif kind=='coop_frame':
                frames.append(value)
                print(f"Cycle {len(frames)}: {value['duration']:.3f}s, {value['slots']} slots, "
                      f"valid={value['valid']}: {value['reason']}", flush=True)
            elif kind in ('fleet','auto_reconnecting','multi_failed'): print(kind,value,flush=True)
        fleet=CooperativeFleet(plan,emit,mode='xy')
        try:
            await fleet.start(background=False)
            result['uwb_status']={name:await session.command('uwb status')
                                  for name,session in fleet.connections.items()}
            for _ in range(cycles): await fleet.cycle()
            result['imu_status']={name:await session.command('imu status')
                                  for name,session in fleet.connections.items()}
        except Exception as exc:
            result['error']=str(exc)
            print('ERROR',str(exc),flush=True)
        finally:
            result['stop_errors']=await fleet.stop()
            result['summary']=dict(cycles=len(frames),valid_frames=sum(f['valid'] for f in frames),
                median_cycle_seconds=statistics.median(f['duration'] for f in frames) if frames else None,
                successful_slots=sum(s['distance'] is not None for s in slots),total_slots=len(slots),
                tag_tag_success=sum(s['a'].startswith('B') and s['b'].startswith('B') and s['distance'] is not None for s in slots))
            (output/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result['summary']),flush=True)
    return 0 if len(frames)==cycles and any(f['valid'] for f in frames) and not result['stop_errors'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cycles',type=int,default=20)
    args=parser.parse_args()
    if args.cycles<1: parser.error('--cycles must be positive')
    raise SystemExit(asyncio.run(run(json.loads(args.plan.read_text()),args.output,args.cycles)))
