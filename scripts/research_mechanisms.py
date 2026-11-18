#!/usr/bin/env python3
"""Controlled falsification experiments for admission and scheduler regressions."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim import Config, NodeSpec
from kv_sim.workload.hotspot import make_hotspot_workload, make_hotspot_initial_caches
from scripts.research_common import (AuditSimulator, assert_invariants, archive,
    digest, metrics, new_directory, provenance, write_csv)


def simulate(seed, policy, admission='always', capacity=256, source_pin=False,
             allow_remote=True, force=None, nic=4., fabric=4., audit=True):
    specs = [NodeSpec(i, capacity, nic) for i in range(4)]
    reqs, hot = make_hotspot_workload(seed=seed, burst_width=1)
    initial = make_hotspot_initial_caches(specs, hot, seed=seed)
    sim = AuditSimulator(specs, Config(policy=policy, admission=admission,
        fabric_gib_s=fabric, trace_decisions=audit), hot=hot, audit=audit,
        allow_remote=allow_remote, force=force)
    for i, keys in initial.items():
        sim.seed_cache(i, keys)
    if source_pin:
        sim.nodes[0].cache.pin(hot)
    sim.run(reqs)
    if source_pin:
        assert sim.nodes[0].cache.pins == {k: 1 for k in hot}
        sim.nodes[0].cache.unpin(hot)
    assert_invariants(sim)
    return sim, digest([r.__dict__ for r in reqs]), digest(initial)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--archive', type=Path)
    p.add_argument('--seeds', type=int, nargs='+', default=[41,42,43,44,45])
    args = p.parse_args()
    out = new_directory(args.out)
    controls = [dict(name='reference'), dict(name='capacity260', capacity=260),
        dict(name='source_pin', source_pin=True), dict(name='no_fetch', allow_remote=False),
        dict(name='nic16', nic=16.), dict(name='fabric16', fabric=16.)]
    config = dict(seeds=args.seeds, requests=240, shared_blocks=256,
        suffix_blocks=4, burst_width=1, burst_gap_ms=2., nodes=4,
        nic=4., fabric=4., capacity=256, controls=controls,
        interpretation='Mechanism study on previously inspected seeds; not held-out evaluation.')
    (out/'config.json').write_text(json.dumps(provenance(config), indent=2)+'\n')
    results, equivalence, divergences = [], [], []
    for seed in args.seeds:
        reference = {}
        for policy in ('greedy', 'bandwidth_aware'):
            for admission in ('always', 'no_remote'):
                sim, trace_hash, cache_hash = simulate(seed, policy, admission, audit=(seed == 42))
                reference[policy, admission] = sim
                if seed == 42:
                    sim.save_traces(out/('seed%d-%s-%s' % (seed,policy,admission)))
                results.append(dict(seed=seed, control='reference', trace_hash=trace_hash,
                                    initial_cache_hash=cache_hash, **metrics(sim)))
            a, b = reference[policy,'always'], reference[policy,'no_remote']
            equal_rows = a.rows == b.rows
            equal_decisions = a.decisions == b.decisions
            cache_equal = [list(a.nodes[i].cache.blocks)==list(b.nodes[i].cache.blocks) for i in a.nodes]
            differing = sum(x['after_digest'] != y['after_digest'] for x,y in zip(a.admissions,b.admissions))
            equivalence.append(dict(seed=seed, policy=policy, request_outcomes_equal=equal_rows,
                candidate_decisions_equal=equal_decisions if seed==42 else '',
                final_caches_equal=all(cache_equal), final_cache_equal_by_node=json.dumps(cache_equal),
                different_post_admission_states=differing if seed==42 else '',
                remote_requests=sum(r['remote_hit_tokens']>0 for r in a.rows)))
        g, a = reference['greedy','always'], reference['bandwidth_aware','always']
        gm, am = {r['request_id']:r for r in g.rows}, {r['request_id']:r for r in a.rows}
        rid = next((rid for rid in g.arrival_order if gm[rid]['node']!=am[rid]['node']), None)
        if rid:
            divergences.append(dict(seed=seed,request_id=rid,greedy_node=gm[rid]['node'],aware_node=am[rid]['node']))
        for control in controls[1:] + ([dict(name='force_first_divergence', force={rid:gm[rid]['node']})] if rid else []):
            kwargs = {k:v for k,v in control.items() if k!='name'}
            for policy in ('greedy','bandwidth_aware'):
                sim, th, ch = simulate(seed, policy, audit=False, **kwargs)
                results.append(dict(seed=seed,control=control['name'],trace_hash=th,
                                    initial_cache_hash=ch,**metrics(sim)))
        write_csv(out/'runs.csv',results)
        write_csv(out/'equivalence.csv',equivalence)
        write_csv(out/'first_divergence.csv',divergences)
        print('Completed seed',seed,flush=True)
    if args.archive:
        archive(out,args.archive,['runs.csv','equivalence.csv','first_divergence.csv'],
            '# Regression mechanism interventions\n\nSynthetic experiments. Read docs/RESEARCH_LOG.md for interpretation.\n'
            'Seeds 41–45 are exploratory. Each control changes only its named intervention; '
            'forcing the first divergence is a single placement intervention, not a causal decomposition of the entire trajectory.\n'
            'Full per-request admissions, queues, predictions and cache contents remain server-side; hashes in manifest.json.')


if __name__=='__main__':
    main()
