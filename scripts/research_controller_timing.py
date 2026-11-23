#!/usr/bin/env python3
"""Compare placement snapshots at start and end of a serial controller interval."""
import argparse
from collections import deque
import csv
from functools import partial
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim.engine.events import EVENT_ARRIVAL, Request
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_rollout_efficiency import FINITE_SEEDS, STRESS
from scripts.research_shadow import MeasuredSimulator, inputs, summaries, trial

SEEDS = {'development': (2700, 2701), 'evaluation': (2601, 2602, 2603, 2604, 2605)}
POLICIES = ('round_robin_cost', 'shadow_cluster_lru', 'shadow_cluster_finite')
MODES = {'zero': ('finish', 0.), 'start_quarter': ('start', .25),
         'finish_quarter': ('finish', .25), 'start_full': ('start', 1.),
         'finish_full': ('finish', 1.)}


class ControllerSimulator(MeasuredSimulator):
    """FCFS controller computes on a snapshot and commits when its CPU slot ends.

    A frozen placement has no node reservation. Execution replans the KV
    route when the request reaches service, as in the default engine.
    """
    def __init__(self, *args, placement_ms, snapshot, **kwargs):
        super().__init__(*args, **kwargs)
        if placement_ms < 0 or snapshot not in ('start', 'finish'):
            raise ValueError('invalid controller parameters')
        self.placement_ms = placement_ms
        self.snapshot = snapshot
        self.pending = deque()
        self.placement_active = False
        self.controller_trace = []

    def _decision(self, req, dispatch_ms):
        chosen = self._choose(req)
        forecast = self.selected_forecasts[req.request_id]
        delay = dispatch_ms - req.arrival_ms
        forecast['queue_ms'] += delay
        forecast['estimated_ms'] += delay
        return chosen

    def _begin(self, req):
        self.placement_active = True
        end = self.now + self.placement_ms
        chosen = self._decision(req, end) if self.snapshot == 'start' else None
        # Reuse the external-arrival class so private service forks omit
        # this callback just as they omit all undispatched requests.
        self._schedule(end, EVENT_ARRIVAL, (req, self.now, chosen))

    def _on_arrival(self, event):
        if not self.placement_ms:
            super()._on_arrival(event)
            return
        if isinstance(event, Request):
            if self.placement_active:
                self.pending.append(event)
            else:
                self._begin(event)
            return

        req, started, chosen = event
        if self.snapshot == 'finish':
            chosen = self._decision(req, self.now)
        self.controller_trace.append(dict(request_id=req.request_id, node=chosen,
            arrival_ms=req.arrival_ms, start_ms=started, dispatch_ms=self.now))
        self._dispatch(req, chosen)
        self.placement_active = False
        if self.pending:
            self._begin(self.pending.popleft())


def paired(rows):
    groups = {}
    for row in rows:
        group = groups.setdefault((row['scenario'], row['seed']), {})
        group[row['mode'], row['policy']] = row
    comparisons = []
    for (scenario, seed), group in sorted(groups.items()):
        assert set(group) == {(mode, policy) for mode in MODES for policy in POLICIES}
        assert len({r['trace_hash'] for r in group.values()}) == 1
        assert len({r['initial_cache_hash'] for r in group.values()}) == 1
        targets = []
        for mode in MODES:
            for policy in POLICIES[1:]:
                targets.append(((mode, policy), (mode, POLICIES[0])))
            targets.append(((mode, POLICIES[2]), (mode, POLICIES[1])))
        for scale in ('quarter', 'full'):
            for policy in POLICIES:
                targets.append((('start_'+scale, policy), ('finish_'+scale, policy)))
        for candidate, baseline in targets:
            c = group[candidate]['p95_ttft_ms']
            b = group[baseline]['p95_ttft_ms']
            delta = 100*(1-c/b)
            comparisons.append(dict(scenario=scenario, seed=seed,
                candidate='/'.join(candidate), baseline='/'.join(baseline),
                candidate_p95_ms=c, baseline_p95_ms=b, p95_improvement_pct=delta,
                outcome='improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral'))
    return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--timing', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    with args.timing.open(newline='') as handle:
        development = list(csv.DictReader(handle))
    if {int(row['seed']) for row in development} != set(FINITE_SEEDS['development']):
        parser.error('placement timing requires the declared finite-policy development seeds')
    rates = {policy: statistics.mean(float(r['decision_mean_us'])
                                    for r in development if r['policy'] == policy)/1000
             for policy in POLICIES}
    out = new_directory(args.out)
    admission = 'always'
    config = provenance(dict(split=args.split, seeds=SEEDS[args.split], scenarios=STRESS,
        policies=POLICIES, admission=admission, requests=120, modes=MODES,
        rates_ms=rates, timing_source=str(args.timing),
        timing_sha256=hashlib.sha256(args.timing.read_bytes()).hexdigest(),
        controller='Serial FCFS, constant development placement-only CPU per policy. '
                   'No node reservation; execution replans at service. Controller time is in TTFT.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows = []
    with gzip.GzipFile(str(out/'traces.jsonl.gz'), 'wb', mtime=0) as trace:
        for scenario in STRESS:
            for seed in SEEDS[args.split]:
                prepared = inputs(scenario, seed, 120)
                order = sorted(((mode, policy) for mode in MODES for policy in POLICIES),
                               key=lambda key: digest([scenario['name'], seed, key]))
                for mode, policy in order:
                    snapshot, scale = MODES[mode]
                    cost = rates[policy]*scale
                    factory = partial(ControllerSimulator, placement_ms=cost, snapshot=snapshot)
                    sim, row = trial(scenario, seed, admission, policy,
                                     prepared=prepared, simulator_factory=factory)
                    controller = sim.controller_trace
                    assert not sim.placement_active and not sim.pending
                    assert len(controller) == (120 if cost else 0)
                    row.update(mode=mode, modeled_placement_ms=cost,
                        mean_controller_ms=statistics.mean(d['dispatch_ms']-d['arrival_ms']
                                                           for d in controller) if controller else 0.,
                        assignment_hash=digest(sorted((r['request_id'], r['node']) for r in sim.rows)),
                        dispatch_times_hash=digest([d['dispatch_ms'] for d in controller]))
                    trace.write((json.dumps(dict(scenario=row['scenario'], seed=seed, mode=mode,
                        policy=policy, controller=controller, requests=sim.rows,
                        predictions=sim.residuals), separators=(',', ':'))+'\n').encode())
                    rows.append(row)
                group = {(r['mode'], r['policy']): r for r in rows[-len(order):]}
                for policy in POLICIES:
                    for scale in ('quarter', 'full'):
                        assert (group['start_'+scale, policy]['dispatch_times_hash']
                                == group['finish_'+scale, policy]['dispatch_times_hash'])
                write_csv(out/'runs.csv', rows)
            print('Completed', args.split, scenario['name'], len(rows), flush=True)
    pairs = paired(rows)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', summaries(pairs))
    if args.archive:
        archive(out, args.archive, ('runs.csv', 'pairs.csv', 'summary.csv'),
                '# Controller snapshot timing\n\n'
                'Paired simulated TTFT with equal dispatch timestamps for each snapshot comparison. '
                'Placement CPU is a constant proxy from development timing.')


if __name__ == '__main__':
    main()
