#!/usr/bin/env python3
"""Interleaved private-cache microbenchmark; cache-copy setup excluded equally."""
import argparse
from collections import OrderedDict
import copy
import json
from pathlib import Path
import statistics
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kv_sim import Request
from kv_sim.cache import BlockCache
from kv_sim.scheduler.shadow import complete_cache
from scripts.research_common import new_directory,provenance,write_csv


def sequential(cache,req,local):
    cache.touch(req.hashes[:local]);cache.admit(req.hashes)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();out=new_directory(args.out)
    cases=[('hot',1024,512,512),('cold',1024,512,0),('oversized',256,256,256)]
    samples=[]
    for case,capacity,shared,local in cases:
        root=BlockCache(capacity)
        root.admit(tuple('hot-%d'%i for i in range(shared))+tuple('cold-%d'%i for i in range(capacity-shared)))
        hashes=tuple(('%s-%d'%('alt'if case=='cold'else'hot',i))for i in range(shared))+tuple('unique-%d'%i for i in range(4))
        req=Request(case,0,hashes,len(hashes)*16)
        for rep in range(-20,500):
            a,b=BlockCache(capacity),BlockCache(capacity)
            a.blocks,b.blocks=root.blocks.copy(),root.blocks.copy()
            calls=[('sequential',lambda:sequential(a,req,local)),
                   ('batch',lambda:complete_cache(b,req,0,local,'always'))]
            if rep%2:calls.reverse()
            timing={}
            for name,fn in calls:
                start=time.perf_counter_ns();fn();timing[name]=(time.perf_counter_ns()-start)/1000
            assert tuple(a.blocks)==tuple(b.blocks)
            if rep>=0:samples.append(dict(case=case,repeat=rep,sequential_us=timing['sequential'],batch_us=timing['batch']))
    write_csv(out/'samples.csv',samples)
    summary=[]
    for case,_,_,_ in cases:
        rr=[r for r in samples if r['case']==case]
        a,b=[statistics.median(r[key]for r in rr)for key in ('sequential_us','batch_us')]
        summary.append(dict(case=case,paired_samples=len(rr),sequential_median_us=a,batch_median_us=b,ratio_of_medians=a/b))
    write_csv(out/'summary.csv',summary)
    (out/'config.json').write_text(json.dumps(provenance(dict(cases=cases,repeats=500,warmups=20,
        order='Alternating AB/BA',timing='Only private touch/admit; equal setup excluded. Shared-host CPU, not GPU/NIC.')),
        indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
