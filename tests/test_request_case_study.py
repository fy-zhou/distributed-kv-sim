"""Request-level case replay regression checks (small synthetic fixture)."""
import unittest

from scripts.request_case_study import (
    select_cases, simulate, pair_requests, verify_archive,
    sample_candidate_decisions)


class RequestCaseStudyTests(unittest.TestCase):
    def test_select_cases_from_archive_like_rows(self):
        base=dict(seed="41",nic_gib_s="1",fabric_gib_s="1",
                  weight="1",greedy_p95_ms="100",aware_p95_ms="110")
        rows=[
            dict(base,cache_blocks="256",burst_width="1",
                 p95_improvement_pct="-10"),
            dict(base,cache_blocks="512",burst_width="8",
                 p95_improvement_pct="15"),
        ]
        cases=select_cases(rows)
        self.assertEqual([c[0] for c in cases],
                         ["regression","improvement"])

    def test_sampled_decisions_preserve_one_policy_field(self):
        params={"nodes":3,"requests":8,"shared_blocks":8,
                "burst_gap_ms":2.0}
        greedy=simulate(params,"greedy",3,1.,1.,24,4,0)
        aware=simulate(params,"bandwidth_aware",3,1.,1.,24,4,1)
        req_id=greedy.arrival_order[0]
        rows=sample_candidate_decisions((greedy,aware),{req_id})
        self.assertEqual(len(rows),6)
        self.assertEqual({r["policy"] for r in rows},
                         {"greedy","bandwidth_aware"})
        self.assertTrue(all(r["request_id"]==req_id for r in rows))

    def test_replay_decomposition_and_archive_checks(self):
        params={"nodes":3,"requests":16,"shared_blocks":8,
                "burst_gap_ms":2.0}
        x=simulate(params,"greedy",3,1.,1.,24,4,0)
        y=simulate(params,"bandwidth_aware",3,1.,1.,24,4,1)
        paired=pair_requests(x,y)
        self.assertEqual(len(paired),16)
        self.assertEqual(len(x.decisions),16*3)
        self.assertEqual(len(y.decisions),16*3)
        self.assertEqual(len(x.cache_trace),16*3)
        self.assertEqual(len(y.cache_trace),16*3)
        def source_stats(sim):
            source=[r for r in sim.cache_trace if r["node"]==0]
            return (sum(r["hot_prefix_resident_blocks"] for r in source) /
                    len(source),source[-1]["hot_prefix_resident_blocks"],
                    sim.nodes[0].cache.evictions)
        gx=source_stats(x)
        gy=source_stats(y)
        record={
            "greedy_p95_ms":str(x.summary()["p95_ttft_ms"]),
            "aware_p95_ms":str(y.summary()["p95_ttft_ms"]),
            "changed_placements":str(sum(r["placement_changed"] for r in paired))}
        for prefix,values in (("greedy",gx),("aware",gy)):
            record[prefix+"_source_hot_mean_blocks"]=str(values[0])
            record[prefix+"_source_hot_final_blocks"]=str(values[1])
            record[prefix+"_source_evictions"]=str(values[2])
        verify_archive(x,y,record)
        record["aware_p95_ms"]="0"
        with self.assertRaises(AssertionError):
            verify_archive(x,y,record)


if __name__=="__main__":
    unittest.main()
