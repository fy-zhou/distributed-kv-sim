import unittest
from kv_sim import Simulator,Config
from scripts.research_shadow import (inputs,trial,POLICIES,comparisons,SEEDS)


class ShadowExperimentTests(unittest.TestCase):
    def test_common_instrumentation_preserves_execution_and_counts_each_plan(self):
        scenario=dict(name='small',nodes=2,shared=4,capacity=16)
        params,specs,reqs,hot,initial=inputs(scenario,21,8)
        for policy in ('round_robin_cost','cost_aware','shadow_cache','shadow_bounded'):
            measured,_=trial(scenario,21,'always',policy,8)
            plain=Simulator(specs,Config(policy=policy,fabric_gib_s=params['fabric']))
            for node,keys in initial.items():plain.seed_cache(node,keys)
            plain.run(reqs)
            self.assertEqual(measured.rows,plain.rows)
            self.assertEqual(len(measured.execution_plan_ns),8)
            self.assertEqual(len(measured.decision_ns),8)

    def test_protocol_pairs_and_seeds(self):
        self.assertFalse(set(SEEDS['development'])&set(SEEDS['evaluation']))
        rows=[trial(dict(name='small',nodes=2,shared=4,capacity=16),21,'always',p,6)[1]for p in POLICIES]
        self.assertEqual(len(comparisons(rows)),8)
        with self.assertRaises(ValueError):comparisons(rows[:-1])
        with self.assertRaises(ValueError):comparisons(rows+[rows[0]])
