#!/usr/bin/env python3
"""Bounded single-host pinned-memory H2D/D2H measurement; never inter-node NIC data.

Uses an existing torch environment. At most 128 MiB tensor allocation on one
idle GPU and 128 MiB pinned host tensors, plus CUDA context/allocator overhead.
A shell timeout should additionally bound process lifetime. Does not load a model.
"""
import argparse
import csv
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.research_common import new_directory, provenance, write_csv


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gpu',type=int,default=0)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--direction',choices=('h2d','d2h'),default='h2d')
    args=p.parse_args()
    sample=subprocess.check_output(['nvidia-smi','-i',str(args.gpu),
        '--query-gpu=uuid,name,driver_version,pci.bus_id,memory.used,utilization.gpu',
        '--format=csv,noheader,nounits'],universal_newlines=True).strip()
    parts=next(csv.reader([sample]))
    if float(parts[-2])>200 or float(parts[-1])>10:
        raise SystemExit('Selected GPU is busy; measurement not started: '+sample)
    # Limit torch visibility before importing CUDA libraries.
    os.environ['CUDA_VISIBLE_DEVICES']=parts[0].strip()
    import torch
    torch.set_num_threads(1)
    out=new_directory(args.out)
    deadline=time.monotonic()+25
    config=provenance(dict(kind='single_host_contiguous_pinned_'+args.direction.upper(),gpu=args.gpu,
        samples=15,warmups=3,sizes_mib=[.0625,1,16,64],streams=[1,2],
        max_gpu_tensor_bytes=128*2**20,max_host_pinned_bytes=128*2**20,
        time_limit_seconds=25,gpu_before=sample,torch=torch.__version__,
        cuda_runtime=torch.version.cuda,device_name=torch.cuda.get_device_name(0),
        cpu_affinity=sorted(os.sched_getaffinity(0)),
        topology=subprocess.check_output(['nvidia-smi','topo','-m'],universal_newlines=True),
        note='No model or KV scatter; device event makespan includes launch gaps. No NIC measurement.'))
    config['synthetic']=False
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    rows=[]
    for mib in (.0625,1,16,64):
        for concurrency in (1,2):
            # Reject a competing process before each cell, while allowing this PID.
            apps=subprocess.check_output(['nvidia-smi','-i',str(args.gpu),
                '--query-compute-apps=pid','--format=csv,noheader,nounits'],universal_newlines=True)
            if any(line.strip() and line.strip()!=str(os.getpid()) for line in apps.splitlines()):
                raise SystemExit('Another compute process appeared; partial measurements preserved.')
            size=int(mib*2**20)
            host=[torch.full((size,),i+1,dtype=torch.uint8,pin_memory=True)for i in range(concurrency)]
            device=[torch.empty((size,),dtype=torch.uint8,device='cuda')for _ in range(concurrency)]
            if args.direction=='d2h':
                for i in range(concurrency):
                    device[i].fill_(i+2)
            streams=[torch.cuda.Stream()for _ in range(concurrency)]
            for rep in range(-3,15):
                if time.monotonic()>deadline:raise SystemExit('Measurement time limit reached; partial results preserved.')
                torch.cuda.synchronize()
                start,end=torch.cuda.Event(enable_timing=True),torch.cuda.Event(enable_timing=True)
                done=[torch.cuda.Event()for _ in streams]
                wall_start=time.perf_counter()
                start.record()
                for i,stream in enumerate(streams):
                    stream.wait_event(start)
                    with torch.cuda.stream(stream):
                        if args.direction=='h2d':
                            device[i].copy_(host[i],non_blocking=True)
                        else:
                            host[i].copy_(device[i],non_blocking=True)
                        done[i].record()
                for event in done:torch.cuda.current_stream().wait_event(event)
                end.record();end.synchronize()
                wall_ms=(time.perf_counter()-wall_start)*1000
                cuda_ms=start.elapsed_time(end)
                if rep>=0:
                    rows.append(dict(payload_per_stream_bytes=size,streams=concurrency,repeat=rep,
                        total_bytes=size*concurrency,wall_ms=wall_ms,cuda_makespan_ms=cuda_ms,
                        aggregate_gib_s=size*concurrency/2**30/(cuda_ms/1000)))
            # Validate first/last bytes without allocating a large readback.
            destination, offset = (device, 1) if args.direction=='h2d' else (host, 2)
            for i,tensor in enumerate(destination):
                assert tensor[0].item()==i+offset and tensor[-1].item()==i+offset
            del destination
            del tensor  # Do not retain the previous cell through the loop variable.
            del host,device,streams
            torch.cuda.empty_cache()
            write_csv(out/'samples.csv',rows)
    summary=[]
    for size,concurrency in sorted({(r['payload_per_stream_bytes'],r['streams'])for r in rows}):
        rr=[r for r in rows if r['payload_per_stream_bytes']==size and r['streams']==concurrency]
        median=statistics.median(r['cuda_makespan_ms']for r in rr)
        total=size*concurrency
        summary.append(dict(payload_per_stream_bytes=size,streams=concurrency,samples=len(rr),
            median_cuda_ms=median,median_wall_ms=statistics.median(r['wall_ms']for r in rr),
            aggregate_gib_s=total/2**30/(median/1000),
            old_single_copy_predictor_ms=(1.684+71.210*total/2**30
                                          if args.direction=='h2d' else '')))
    write_csv(out/'summary.csv',summary)
    peak_bytes=torch.cuda.max_memory_allocated()
    assert peak_bytes <= 128*2**20, 'GPU tensor allocation exceeded declared bound'
    (out/'completion.json').write_text(json.dumps(dict(samples=len(rows),
        peak_tensor_bytes=peak_bytes,
        verified_readback=True,elapsed_seconds=25-(deadline-time.monotonic())),indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
