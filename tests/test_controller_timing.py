import unittest

from kv_sim import Config, NodeSpec, Request
from scripts.research_common import assert_invariants
from scripts.research_controller_timing import ControllerSimulator


class ObservedController(ControllerSimulator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.decision_states = []

    def _choose(self, req):
        self.decision_states.append((req.request_id, self.now, self.nodes[0].active is not None))
        return super()._choose(req)


class ControllerTimingTests(unittest.TestCase):
    def test_equal_dispatch_pacing_can_expose_different_worker_state(self):
        requests = [Request(str(i), 0., (), 1) for i in range(2)]
        traces = []
        for snapshot in ('start', 'finish'):
            sim = ObservedController([NodeSpec(0, 1)], Config(policy='round_robin_cost',
                gpu_ms_per_new_token=0., gpu_ms_per_attention_pair=0.), audit=False,
                placement_ms=5., snapshot=snapshot)
            rows = sim.run(requests)
            self.assertEqual([row['queue_ms'] for row in rows], [5., 10.])
            self.assertEqual([row['ttft_ms'] for row in rows], [7., 12.])
            self.assertFalse(sim.pending)
            self.assertFalse(sim.placement_active)
            assert_invariants(sim)
            traces.append(sim.controller_trace)
            expected = ([('0', 0., False), ('1', 5., True)] if snapshot == 'start'
                        else [('0', 5., False), ('1', 10., False)])
            self.assertEqual(sim.decision_states, expected)
        self.assertEqual(traces[0], traces[1])
