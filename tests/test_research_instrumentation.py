import unittest
from kv_sim import Config, NodeSpec, Simulator, Request
from scripts.research_common import AuditSimulator, assert_invariants


class ResearchInstrumentationTests(unittest.TestCase):
    def test_instrumentation_preserves_behavior_and_counts_admissions(self):
        specs=[NodeSpec(i,4,.1) for i in range(2)]
        reqs=[Request.from_tokens(str(i),i,list(range(80))) for i in range(8)]
        a=Simulator(specs,Config(policy='round_robin'))
        b=AuditSimulator(specs,Config(policy='round_robin'),hot=reqs[0].hashes[:4])
        for sim in (a,b):
            sim.seed_cache(1,reqs[0].hashes[:4]);sim.run(reqs)
        self.assertEqual(a.rows,b.rows)
        self.assertEqual(a.summary(),b.summary())
        for node in a.nodes:
            self.assertEqual(list(a.nodes[node].cache.blocks),list(b.nodes[node].cache.blocks))
        self.assertEqual(len(b.admissions),8)
        self.assertEqual(len(b.residuals),8)
        for row in b.admissions:
            self.assertEqual(row['proposed'],row['inserted']+row['rejected']+row['touched'])
        assert_invariants(b)
