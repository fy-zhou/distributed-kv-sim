import unittest
from scripts.research_externality_probe import probe


class ExternalityProbeTests(unittest.TestCase):
    def test_exact_small_and_equal_transfer_counterfactuals(self):
        small=probe(1)
        equal=probe(1000)
        self.assertAlmostEqual(small['exact_externality_ms'],.01)
        self.assertAlmostEqual(equal['exact_externality_ms'],10.)
        self.assertAlmostEqual(small['overestimate_factor'],1000.)
