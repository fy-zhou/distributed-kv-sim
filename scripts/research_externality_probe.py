#!/usr/bin/env python3
"""Falsify the infinite-overlap externality assumption with exact fluid replay."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kv_sim import Config,NodeSpec,Request,Simulator
from kv_sim.cluster.network import FluidNetwork
from scripts.research_common import archive,new_directory,provenance,write_csv


def probe(candidate_bytes):
    sim=Simulator([NodeSpec(i,candidate_bytes+1,1.)for i in range(3)],
                  Config(block_size=1,kv_bytes_per_token=1,remote_setup_ms=0.))
    sim.network=FluidNetwork({0:100.,1:100.,2:100.},1000.)
    req=Request.from_tokens('candidate',0,list(range(candidate_bytes)),block_size=1)
    sim.seed_cache(0,req.hashes)
    incumbent=sim.network.start(0,0,1,1000)
    predicted=sim._externality_ms(req,2)
    sim.network.start(0,0,2,candidate_bytes)
    actual_finish=None
    now=0.
    while sim.network.flows:
        now,fid=sim.network.next_finish(now)
        sim.network.finish(now,fid)
        if fid==incumbent:actual_finish=now
    exact=actual_finish-10.
    return dict(candidate_bytes=candidate_bytes,incumbent_bytes=1000,
        predicted_externality_ms=predicted,exact_externality_ms=exact,
        overestimate_factor=predicted/exact)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--archive',type=Path,required=True)
    args=p.parse_args()
    out=new_directory(args.out)
    rows=[probe(size)for size in (1,10,100,1000,10000)]
    write_csv(out/'externality.csv',rows)
    (out/'config.json').write_text(json.dumps(provenance(dict(
        setup_ms=0.,nic_bytes_per_ms=100.,fabric_bytes_per_ms=1000.,
        incumbent_src=0,incumbent_dst=1,candidate_src=0,candidate_dst=2,
        interpretation='Isolates finite flow duration. No queues, cache evolution, or future arrivals.')),
        indent=2)+'\n')
    archive(out,args.archive,['externality.csv'],
        '# Finite-flow externality probe\n\n'
        'Exact deterministic fluid replay versus the existing frozen-rate approximation.\n'
        'For a 1-byte candidate competing with a 1000-byte incumbent, the heuristic predicts '
        '10 ms added delay; replay gives 0.01 ms (1000× overestimate). '
        'At candidate sizes >= incumbent it is exact in this two-flow topology. '
        'This falsifies universal finite-transfer accuracy, not the documented approximation itself. '
        'Queued starts, setup and future arrivals create additional uncertainty. '
        'No baseline or candidate behavior was changed after evaluation.')
    print(rows)


if __name__=='__main__':main()
