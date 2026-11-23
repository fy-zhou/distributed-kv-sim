import unittest

from kv_sim import Config, NodeSpec, Request
from kv_sim.cache import BlockCache, admitted_blocks
from kv_sim.scheduler.shadow import complete_cache, complete_unpinned_lru
from scripts.research_common import AuditSimulator, assert_invariants


class PinAwareAdmissionTests(unittest.TestCase):
    def test_pinned_prefix_members_and_duplicate_keys_consume_no_extra_slot(self):
        blocks = ('w', 'a', 'a', 'b', 'c')
        pins = {'w': 2, 'x': 1, 'z': 1}
        self.assertEqual(admitted_blocks(blocks, 0, 'prefix_fit_pins', capacity=4, pins=pins),
                         ('w', 'a', 'a'))
        self.assertEqual(admitted_blocks(blocks, 0, 'prefix_fit_pins', capacity=4), blocks[:4])

    def test_both_local_forecast_cache_implementations_preserve_prefix(self):
        for update in (complete_cache, complete_unpinned_lru):
            cache = BlockCache(4, 'prefix_fit_pins')
            cache.admit(('w', 'x', 'j', 'k'))
            cache.pin(('w', 'x'))
            req = Request('r', 0., ('a', 'b', 'c', 'd'), 64)
            update(cache, req, 0, 0, 'prefix_fit_pins')
            self.assertEqual(tuple(cache.blocks), ('w', 'x', 'a', 'b'))
            self.assertEqual(cache.prefix_len(req.hashes), 2)

    def test_real_outgoing_transfer_holds_two_slots_during_admission(self):
        reqs = [Request('copy', 0., ('w', 'x'), 2),
                Request('miss', 0., ('a', 'b', 'c', 'd'), 4)]
        for policy, prefix in (('prefix_fit', 0), ('prefix_fit_pins', 2)):
            sim = AuditSimulator([NodeSpec(i, 4, .01) for i in range(2)],
                Config(admission=policy, policy='round_robin', block_size=1,
                       kv_bytes_per_token=2**20),
                force={'copy': 1, 'miss': 0})
            sim.seed_cache(0, ('w', 'x'))
            sim.run(reqs)
            observed = next(r for r in sim.admissions if r['request_id'] == 'miss')
            self.assertEqual(observed['request_prefix_after'], prefix)
            self.assertGreater(sim.rows[-1]['network_ms'], 0.)
            assert_invariants(sim)
