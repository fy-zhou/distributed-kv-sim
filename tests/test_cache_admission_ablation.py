"""Controlled scheduler/admission ablation test."""
import unittest

from scripts.cache_admission_ablation import make_pairs, run_case


class AdmissionAblationTests(unittest.TestCase):
    def test_two_policies_and_two_admissions(self):
        rows=[]
        for admission in ("always","no_remote"):
            for policy in ("greedy","bandwidth_aware"):
                r=run_case(seed=7,nic=1.,fabric=1.,cache=24,burst=4,
                           admission=admission,policy=policy,
                           requests=12,shared_blocks=8,nodes=3)
                r["case"]="test"
                rows.append(r)
        pairs=make_pairs(rows)
        self.assertEqual(len(pairs),2)
        self.assertEqual({p["admission"] for p in pairs},
                         {"always","no_remote"})
        self.assertTrue(all(p["greedy_p95_ms"]>0 and
                            p["aware_p95_ms"]>0 for p in pairs))

    def test_missing_policy_fails(self):
        with self.assertRaises(ValueError):
            make_pairs([{"case":"x","seed":1,"admission":"always",
                         "policy":"greedy"}])


if __name__=="__main__":
    unittest.main()
