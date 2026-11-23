#!/usr/bin/env python3
"""Explore physical-cost assumptions with a serial CPU materialization proxy."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim import Config
from kv_sim.engine.events import EVENT_JOB_DONE
from scripts.research_common import (archive, assert_invariants, digest, metrics,
                                     new_directory, provenance, write_csv)
from scripts.research_evaluate import SCENARIOS
from scripts.research_shadow import MeasuredSimulator, inputs, summaries, TIMING_FIELDS

SEEDS = {'development': (41, 42), 'evaluation': (401, 402, 403, 404, 405)}
POLICIES = ('round_robin_cost', 'cost_aware', 'shadow_fast')
PROFILES = ('illustrative', 'cold_proxy', 'worker_load_proxy', 'serial_spill_16', 'serial_spill_32')


class SpillSimulator(MeasuredSimulator):
    """Block node release and cache visibility while new full blocks spill."""

    def __init__(self, *args, spill_gib_s=0., **kwargs):
        super().__init__(*args, **kwargs)
        self.spill_gib_s = spill_gib_s
        self.spills = {}

    def _start_compute(self, job):
        super()._start_compute(job)
        if self.spill_gib_s and (self.cfg.admission == 'always' or not job.remote_blocks):
            blocks = len(job.req.hashes)-job.hit
            if blocks:
                size = blocks*self.cfg.block_size*self.cfg.kv_bytes_per_token
                duration_ms = size/2**30/self.spill_gib_s*1000
                self.spills[job.target] = (job.expected_finish_ms, duration_ms, size)
                job.expected_finish_ms += duration_ms

    def _on_job_done(self, node_id):
        spill = self.spills.get(node_id)
        if spill is None:
            super()._on_job_done(node_id)
            self.rows[-1].update(spill_ms=0., spill_bytes=0)
            return
        prefill_done, duration_ms, size = spill
        if self.now == prefill_done:
            self._schedule(self.nodes[node_id].active.expected_finish_ms, EVENT_JOB_DONE, node_id)
            return
        del self.spills[node_id]
        super()._on_job_done(node_id)
        row = self.rows[-1]
        row.update(ttft_ms=prefill_done-row['arrival_ms'], finished_ms=prefill_done,
                   spill_ms=duration_ms, spill_bytes=size)


def trial(scenario, seed, admission, policy, profile, fits):
    params, specs, requests, hot, initial = inputs(scenario, seed, 120)
    config = Config(policy=policy, admission=admission, fabric_gib_s=params['fabric'],
                    gpu_ms_per_new_token=.003*params['compute_scale'],
                    gpu_ms_per_attention_pair=1.8e-6*params['compute_scale'])
    if profile != 'illustrative':
        cold = fits['cold_service_proxy']
        config.gpu_fixed_ms = cold['fixed_ms']
        config.gpu_ms_per_new_token = cold['ms_per_new_token']*params['compute_scale']
        config.gpu_ms_per_attention_pair = cold['ms_per_causal_pair']*params['compute_scale']
    if profile not in ('illustrative', 'cold_proxy'):
        load = fits['transfer_fits']['worker_load_wall_ms']
        config.h2d_fixed_ms, config.h2d_ms_per_gib = load['intercept_ms'], load['ms_per_gib']
    spill = float(profile.rsplit('_', 1)[-1]) if profile.startswith('serial_spill_') else 0.
    sim = SpillSimulator(specs, config, hot=hot, audit=False, spill_gib_s=spill)
    for node, blocks in initial.items():
        sim.seed_cache(node, blocks)
    sim.run(requests)
    assert_invariants(sim)
    assert not sim.spills
    row = dict(scenario=scenario['name'], seed=seed, profile=profile,
               trace_hash=digest([r.__dict__ for r in requests]), initial_cache_hash=digest(initial),
               outcomes_hash=digest(sim.rows), final_cache_hash=digest(
                   {i: list(n.cache.blocks) for i, n in sim.nodes.items()}),
               spill_gib=sum(r['spill_bytes'] for r in sim.rows)/2**30,
               mean_spill_ms=sum(r['spill_ms'] for r in sim.rows)/len(requests),
               **metrics(sim))
    return sim, row


def key(row):
    return row['scenario'], int(row['seed']), row['admission'], row['profile'], row['policy']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--fits', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--verify-against', type=Path)
    args = parser.parse_args()
    out = new_directory(args.out)
    fits = json.loads(args.fits.read_text())
    scenarios = [s for s in SCENARIOS if s['name'] in ('reference', 'long_prefix', 'compute_heavy')]
    config = provenance(dict(seeds=SEEDS[args.split], policies=POLICIES, profiles=PROFILES,
        scenarios=scenarios, requests=120, admissions=('always', 'no_remote'),
        fits_sha256=hashlib.sha256(args.fits.read_bytes()).hexdigest(), fits=fits,
        interpretation='Model sensitivity. Cold fitted coefficients are extrapolated to reuse by the model. '
                       'Worker load replaces H2D component without adding scatter twice. Compute-heavy scales '
                       'variable terms by eight as a synthetic stress. Serial D2H rates are assumptions. '
                       'Spill affects resource release and cache visibility after first-token completion. '
                       'Forecasts do not charge spill for future queued jobs.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    expected = {}
    if args.verify_against:
        with args.verify_against.open(newline='') as handle:
            expected = {key(r): r for r in csv.DictReader(handle)}
    rows, pairs = [], []
    with gzip.GzipFile(str(out/'traces.jsonl.gz'), 'wb', mtime=0) as traces:
        for scenario in scenarios:
            for seed in SEEDS[args.split]:
                for admission in ('always', 'no_remote'):
                    for profile in PROFILES:
                        results = {}
                        for policy in POLICIES:
                            sim, row = trial(scenario, seed, admission, policy, profile, fits)
                            rows.append(row)
                            results[policy] = row
                            if args.verify_against:
                                old = expected.pop(key(row))
                                assert all(old[k] == str(v) for k, v in row.items()
                                           if k not in TIMING_FIELDS)
                            else:
                                traces.write((json.dumps(dict(key=key(row), requests=sim.rows),
                                                          separators=(',', ':'))+'\n').encode())
                        assert len({r['trace_hash'] for r in results.values()}) == 1
                        assert len({r['initial_cache_hash'] for r in results.values()}) == 1
                        for baseline in POLICIES[:-1]:
                            candidate = results['shadow_fast']['p95_ttft_ms']
                            control = results[baseline]['p95_ttft_ms']
                            improvement = 100*(1-candidate/control)
                            label = ('improve' if improvement > 1e-7 else
                                     'regress' if improvement < -1e-7 else 'neutral')
                            pairs.append(dict(scenario=scenario['name'], seed=seed, admission=admission,
                                profile=profile, candidate='shadow_fast', baseline=baseline,
                                candidate_p95_ms=candidate, baseline_p95_ms=control,
                                p95_improvement_pct=improvement, outcome=label))
            write_csv(out/'runs.csv', rows)
            print(args.split, scenario['name'], len(rows), flush=True)
    assert not expected
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', [dict(profile=profile, **r) for profile in PROFILES
        for r in summaries([p for p in pairs if p['profile'] == profile])])
    if args.verify_against:
        (out/'verification.json').write_text(json.dumps(dict(verified_runs=len(rows),
            ignored_fields=TIMING_FIELDS, reference_sha256=hashlib.sha256(
                args.verify_against.read_bytes()).hexdigest()), indent=2)+'\n')
    if args.archive:
        archive(out, args.archive, ['runs.csv', 'pairs.csv', 'summary.csv'],
                '# CPU KV materialization and service model sensitivity\n\n'
                'All latency outcomes are simulated. Fitted cold service and worker load are proxies based on prior measurements. '
                'Serial spill rates are assumed and are not measurements. Physical spill costs may overlap in real systems.')


if __name__ == '__main__':
    main()
