"""Reproducible offline software benchmark; never a radio throughput claim."""
import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path
import psutil
from uwb3d_runtime import Runtime


async def benchmark(seconds,output,count=30,sparse=False):
    runtime=Runtime(asyncio.get_running_loop());process=psutil.Process()
    samples=[];cpu0=time.process_time();start=time.monotonic()
    try:
        await runtime.start('simulation',count=count,scenario='Random motion',sparse=sparse)
        while time.monotonic()-start<seconds:
            await asyncio.sleep(.1);state=runtime.snapshot()
            if not state:continue
            samples.append(dict(elapsed=time.monotonic()-start,rss=process.memory_info().rss,
                state=state['state'],rmse=state.get('simulation_rmse'),**state['metrics']))
        result=dict(source='SIMULATION ONLY',nodes=count,sparse=sparse,seconds=time.monotonic()-start,
                    cpu_core_percent=100*(time.process_time()-cpu0)/(time.monotonic()-start),samples=samples)
        if samples:
            values=lambda k:[s[k] for s in samples if s.get(k) is not None]
            result['summary']=dict(polls=len(samples),measured_supported=sum(s['state']=='measured-supported' for s in samples),
                solver_ms_median=statistics.median(values('solver_ms')),solver_ms_max=max(values('solver_ms')),
                aligned_rmse_m_median=statistics.median(values('rmse')) if values('rmse') else None,
                localization_hz_median=statistics.median(values('localization_hz')[10:] or values('localization_hz')),
                ranges_per_second_median=statistics.median(values('measurement_hz')[10:] or values('measurement_hz')),
                rss_start=samples[0]['rss'],rss_end=samples[-1]['rss'],rss_peak=max(values('rss')),
                queue_drops=samples[-1]['queue_drops'],log_drops=samples[-1]['log_drops'])
        output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2))
        print(json.dumps(result.get('summary',result),indent=2))
    finally:await runtime.shutdown()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seconds',type=float,default=60)
    p.add_argument('--nodes',type=int,default=30);p.add_argument('--sparse',action='store_true')
    p.add_argument('--output',type=Path,default=Path('build/uwb3d/benchmark.json'));a=p.parse_args()
    if a.seconds<=0:p.error('seconds must be positive')
    asyncio.run(benchmark(a.seconds,a.output,a.nodes,a.sparse))
