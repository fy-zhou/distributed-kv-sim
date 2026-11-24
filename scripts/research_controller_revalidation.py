#!/usr/bin/env python3
"""Revalidate frozen placements using only work dispatched since the snapshot."""
import argparse
import csv
from functools import partial
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_controller_timing import ControllerSimulator, POLICIES
from scripts.research_rollout_efficiency import FINITE_SEEDS, STRESS
from scripts.research_shadow import inputs, summaries, trial

SEEDS = {'development': (4600, 4601), 'evaluation': (4701, 4702, 4703, 4704, 4705)}
MODES = ('start', 'revalidate', 'finish')
SCENARIOS = STRESS + (dict(name='sparse_control', compute_scale=16., burst=1, gap=1500.),)
SLOTS = 4


class RevalidatedController(ControllerSimulator):
    """Update scores only for newly committed work, without a second rollout."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.committed_service = dict.fromkeys(self.nodes, 0.)
        self.shortlists = {}
        self.revalidation_ns = []
        self.revised = 0

    def _decision(self, req, dispatch_ms):
        chosen = super()._decision(req, dispatch_ms)
        if self.cfg.policy != POLICIES[0]:
            rid, forecasts = self.shadow_last_forecast
            assert rid == req.request_id
            self.shortlists[req.request_id] = (forecasts, self.committed_service.copy())
        return chosen

    def _placement_at_dispatch(self, req, chosen):
        if self.cfg.policy == POLICIES[0]:
            return chosen
        started = time.perf_counter_ns()
        forecasts, old_commits = self.shortlists.pop(req.request_id)
        deltas = {node: self.committed_service[node]-old_commits[node] for node in forecasts}
        node = min(forecasts, key=lambda i: (forecasts[i]['estimated_ms']+deltas[i], i))
        prediction = dict(forecasts[node])
        self.committed_service[node] += prediction['estimated_ms'] - prediction['queue_ms']
        extra_wait = self.now - req.arrival_ms + deltas[node]
        prediction['queue_ms'] += extra_wait
        prediction['estimated_ms'] += extra_wait
        self.selected_forecasts[req.request_id] = prediction
        self.revised += int(node != chosen)
        self.revalidation_ns.append(time.perf_counter_ns()-started)
        return node


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--timing', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    with args.timing.open(newline='') as handle:
        timing = list(csv.DictReader(handle))
    if {int(r['seed']) for r in timing} != set(FINITE_SEEDS['development']):
        parser.error('placement timing must use declared finite development seeds')
    rates = {p: statistics.mean(float(r['decision_mean_us']) for r in timing if r['policy']==p)/1000
             for p in POLICIES}
    out = new_directory(args.out)
    config = provenance(dict(split=args.split, seeds=SEEDS[args.split], scenarios=SCENARIOS,
        policies=POLICIES, modes=MODES, admission='always', requests=120, slots=SLOTS,
        rates_ms=rates, timing_source=str(args.timing),
        timing_sha256=hashlib.sha256(args.timing.read_bytes()).hexdigest(),
        revalidation='Add the forecast service of all requests committed since this snapshot, '
                     're-rank only the original candidates, then use normal worker execution.',
        scope='Dispatch pacing remains equal. Revalidation CPU measured separately and not simulated. '
              'Already-consumed additions may be overcharged; revalidation does not reforecast cache or network.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows, pairs, timings = [], [], []
    with gzip.GzipFile(str(out/'traces.jsonl.gz'), 'wb', mtime=0) as trace:
        for scenario in SCENARIOS:
            for seed in SEEDS[args.split]:
                prepared = inputs(scenario, seed, 120)
                for policy in POLICIES:
                    group = {}
                    order = sorted(MODES, key=lambda m:digest([scenario['name'], seed, policy, m]))
                    for mode in order:
                        cls = RevalidatedController if mode == 'revalidate' else ControllerSimulator
                        factory = partial(cls, snapshot='finish' if mode == 'finish' else 'start',
                                          placement_ms=rates[policy], slots=SLOTS)
                        sim, row = trial(scenario, seed, 'always', policy, prepared=prepared,
                                         simulator_factory=factory)
                        assert not sim.pending and not sim.placement_active
                        assert not getattr(sim, 'shortlists', {})
                        revisions = getattr(sim, 'revised', 0)
                        row.update(mode=mode, revisions=revisions,
                            assignments=digest(sorted((r['request_id'], r['node']) for r in sim.rows)),
                            dispatch_times=digest([r['dispatch_ms'] for r in sim.controller_trace]))
                        ns = getattr(sim, 'revalidation_ns', [])
                        if ns:
                            timings.append(dict(scenario=scenario['name'], seed=seed, policy=policy,
                                samples=len(ns), mean_revalidation_us=statistics.mean(ns)/1000,
                                max_revalidation_us=max(ns)/1000))
                        trace.write((json.dumps(dict(key=[scenario['name'], seed, policy, mode],
                            controller=sim.controller_trace, requests=sim.rows, forecasts=sim.residuals),
                            separators=(',', ':'))+'\n').encode())
                        group[mode] = row
                        rows.append(row)
                    for field in ('trace_hash', 'initial_cache_hash', 'dispatch_times'):
                        assert len({r[field] for r in group.values()}) == 1
                    if not group['revalidate']['revisions']:
                        assert group['revalidate']['outcomes_hash'] == group['start']['outcomes_hash']
                    for candidate, baseline in (('revalidate', 'start'), ('revalidate', 'finish'), ('start', 'finish')):
                        c, b = group[candidate], group[baseline]
                        delta = 100*(1-c['p95_ttft_ms']/b['p95_ttft_ms'])
                        pairs.append(dict(scenario=scenario['name'], seed=seed, policy=policy,
                            candidate=candidate, baseline=baseline, candidate_p95_ms=c['p95_ttft_ms'],
                            baseline_p95_ms=b['p95_ttft_ms'], p95_improvement_pct=delta,
                            outcome='improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral'))
            write_csv(out/'runs.csv', rows)
            print('Completed', args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'timing.csv', timings)
    write_csv(out/'summary.csv', [dict(policy=policy, **r) for policy in POLICIES
                                 for r in summaries([p for p in pairs if p['policy'] == policy])])
    if args.archive:
        archive(out, args.archive, ('runs.csv', 'pairs.csv', 'summary.csv', 'timing.csv'),
                '# Dispatch-time revalidation under four placement slots\n\n'
                'All three scenarios of snapshot handling have identical dispatch pacing within pairs. '
                'Revalidation uses only already-committed work, with its runtime recorded separately.')


if __name__ == '__main__':
    main()
