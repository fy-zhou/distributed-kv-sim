# Finite-flow externality probe

Exact deterministic fluid replay versus the existing frozen-rate approximation.
For a 1-byte candidate competing with a 1000-byte incumbent, the heuristic predicts 10 ms added delay; replay gives 0.01 ms (1000× overestimate). At candidate sizes >= incumbent it is exact in this two-flow topology. This falsifies universal finite-transfer accuracy, not the documented approximation itself. Queued starts, setup and future arrivals create additional uncertainty. No baseline or candidate behavior was changed after evaluation.
