#!/usr/bin/env python3
"""Pair two fluid rate allocators under identical multi-family workloads."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_sim.cluster.network import FluidNetwork, SHARING_MODELS
from scripts.research_common import archive, digest, new_directory, provenance, write_csv
from scripts.research_shadow import MeasuredSimulator, inputs, summaries, trial

SEEDS = {'development': (2800, 2801), 'evaluation': (2701, 2702, 2703, 2704, 2705)}
POLICIES = ('round_robin_cost', 'bandwidth_aware', 'shadow_cluster_finite')
SCENARIOS = (
    dict(name='fabric_only_control', nic_by_node=(16.,)*4, fabric=2., skew=0.),
    dict(name='slow_endpoint', nic_by_node=(1., 8., 8., 8.), fabric=8., skew=0.),
    dict(name='split_endpoints', nic_by_node=(1., 1., 8., 8.), fabric=6., skew=1.2),
    dict(name='wide_fabric', nic_by_node=(1., 2., 8., 8., 8., 16.), nodes=6, fabric=24., skew=0.),
)


class ObservedNetwork(FluidNetwork):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_ms = 0.
        self.overlap_ms = 0.
        self.integrated_bytes = 0.
        self.overlap_bytes = 0.

    def advance(self, now):
        duration = now - self.last_update
        if self.flows and duration > 0:
            self.active_ms += duration
            delivered = duration*sum(f.rate_bytes_per_ms for f in self.flows.values())
            self.integrated_bytes += delivered
            if len(self.flows) > 1:
                self.overlap_ms += duration
                self.overlap_bytes += delivered
        super().advance(now)


class NetworkAuditSimulator(MeasuredSimulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.network = ObservedNetwork(self.network.nic, self.network.fabric,
                                       sharing=self.network.sharing)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=SEEDS, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    scenarios = tuple(dict(shape, kind='zipf', compute_scale=16., capacity=512,
                           shared=256) for shape in SCENARIOS)
    out = new_directory(args.out)
    config = provenance(dict(split=args.split, seeds=SEEDS[args.split], scenarios=scenarios,
        policies=POLICIES, admission='always', requests=120, allocators=SHARING_MODELS,
        control='Fabric-only fixture imposes the same rates under either allocator.',
        rates='Configured full-duplex endpoint and fabric budgets in a single fluid topology.',
        exposure='Fabric utilization while there are concurrent transfers, not whole-run utilization.'))
    (out/'config.json').write_text(json.dumps(config, indent=2)+'\n')
    rows, pairs = [], []
    for scenario in scenarios:
        for seed in SEEDS[args.split]:
            prepared = inputs(scenario, seed, 120)
            for policy in POLICIES:
                group = {}
                order = sorted(SHARING_MODELS, key=lambda m:digest([scenario['name'], seed, policy, m]))
                for model in order:
                    sim, row = trial(scenario, seed, 'always', policy, prepared=prepared,
                                     simulator_factory=NetworkAuditSimulator, network_sharing=model)
                    network = sim.network
                    assert abs(network.integrated_bytes-network.total_bytes) <= 1e-9*max(
                        1., network.total_bytes)
                    row.update(allocator=model, active_network_ms=network.active_ms,
                        overlapping_network_ms=network.overlap_ms,
                        overlap_fabric_utilization=(network.overlap_bytes
                            / (network.overlap_ms*network.fabric)) if network.overlap_ms else 0.,
                        assignment_hash=digest(sorted((r['request_id'], r['node']) for r in sim.rows)))
                    group[model] = row
                    rows.append(row)
                equal, fair = (group[m] for m in SHARING_MODELS)
                for field in ('trace_hash', 'initial_cache_hash'):
                    assert equal[field] == fair[field]
                if scenario['name'] == 'fabric_only_control':
                    for field in ('outcomes_hash', 'final_cache_hash', 'assignment_hash'):
                        assert equal[field] == fair[field], (scenario['name'], seed, policy, field)
                a, b = fair['p95_ttft_ms'], equal['p95_ttft_ms']
                delta = 100*(1-a/b)
                pairs.append(dict(scenario=scenario['name'], seed=seed, policy=policy,
                    candidate='max_min', baseline='equal_share', candidate_p95_ms=a, baseline_p95_ms=b,
                    p95_improvement_pct=delta,
                    outcome='improve' if delta > 1e-7 else 'regress' if delta < -1e-7 else 'neutral',
                    candidate_gib=fair['network_gib'], baseline_gib=equal['network_gib'],
                    candidate_overlap_utilization=fair['overlap_fabric_utilization'],
                    baseline_overlap_utilization=equal['overlap_fabric_utilization'],
                    assignment_changed=fair['assignment_hash'] != equal['assignment_hash']))
        write_csv(out/'runs.csv', rows)
        print('Completed', args.split, scenario['name'], len(rows), flush=True)
    write_csv(out/'pairs.csv', pairs)
    write_csv(out/'summary.csv', [
        dict(policy=policy, **result) for policy in POLICIES
        for result in summaries([p for p in pairs if p['policy'] == policy])])
    if args.archive:
        archive(out, args.archive, ('runs.csv', 'pairs.csv', 'summary.csv'),
                '# Fluid network allocation sensitivity\n\n'
                'Alternative allocation assumptions on the same synthetic topology and workloads. '
                'Neither allocator is fitted to multi-node data.')


if __name__ == '__main__':
    main()
