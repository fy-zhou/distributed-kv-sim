import unittest
from scripts.research_evaluate import (trial,pair_results,POLICIES,SCENARIOS,SEEDS,cluster_summary)


class EvaluationProtocolTests(unittest.TestCase):
    def test_splits_and_pairing_on_identical_inputs(self):
        self.assertFalse(set(SEEDS['development']) & set(SEEDS['evaluation']))
        rows=[]
        scenario=dict(name='small',nodes=2,shared=4,capacity=8)
        for policy in POLICIES:
            _,row=trial(scenario,11,'always',policy,8);rows.append(row)
        pairs=pair_results(rows)
        self.assertEqual(len(pairs),len(POLICIES)-1)
        summary=cluster_summary(pairs)
        self.assertTrue(all(r['conditions']==1 and r['seed_clusters']==1 for r in summary))
        with self.assertRaises(ValueError):pair_results(rows[:-1])
        with self.assertRaises(ValueError):pair_results(rows+[rows[0]])

    def test_replay_hash_is_deterministic(self):
        scenario=dict(name='small',nodes=2,shared=4,capacity=8)
        _,a=trial(scenario,11,'always','cost_aware',8)
        _,b=trial(scenario,11,'always','cost_aware',8)
        for key in a:
            if not key.startswith('decision_'):self.assertEqual(a[key],b[key])
