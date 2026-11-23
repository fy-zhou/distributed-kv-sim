"""Optional service-action planning, separate from node placement policies.

Legacy policies always take the longest available prefix. Cost-aware policies
compare recomputation, local reuse and each remote source using the same
service predictors as execution. No future cache states are assumed known.
"""


def minimum_service(sim, req, dest, isolated=False):
    local = sim._hit(req, dest)
    # Tuple semantics: consumed local blocks, source, total reused blocks.
    choices = [(0, None, 0)]
    if local:
        choices.append((local, None, local))
    for src in sim.nodes:
        if src == dest:
            continue
        hit = sim._hit(req, src)
        if hit > local:
            choices.append((local, src, hit))

    def score(choice):
        consumed_local, source, hit = choice
        remote_ms = 0.0
        if source is not None:
            payload = (hit-consumed_local) * sim.cfg.block_size * sim.cfg.kv_bytes_per_token
            rate = (min(sim.network.nic[source], sim.network.nic[dest], sim.network.fabric)
                    if isolated else sim.network.prospective_rate(source, dest))
            remote_ms = sim.transfer_predictor.predict_ms(payload, rate)
        service = remote_ms + sim._h2d_time(hit) + sim._compute_time(req, hit)
        # Stable ties: avoid transfers, then prefer existing local reuse.
        return service, source is not None, -hit, -1 if source is None else source

    return min(choices, key=score)


REUSE_PLANNERS = {'cost_aware': minimum_service,
                  'round_robin_cost': minimum_service,
                  'shadow_frozen': minimum_service,
                  'shadow_cache': minimum_service,
                  'shadow_bounded': minimum_service,
                  'shadow_fast': minimum_service,
                  'shadow_network': minimum_service,
                  'shadow_fast_lru': minimum_service,
                  'shadow_cluster': minimum_service,
                  'shadow_cluster_pruned': minimum_service,
                  'shadow_cluster_fast': minimum_service,
                  'shadow_cluster_lru': minimum_service,
                  'shadow_cluster_indexed': minimum_service,
                  'shadow_cluster_gated': minimum_service,
                  'shadow_cluster_finite': minimum_service,
                  'shadow_cluster_events': minimum_service,
                  'shadow_cold_queue': minimum_service}


def choose_reuse(sim, req, node_id, isolated=False):
    planner = REUSE_PLANNERS.get(sim.cfg.policy)
    if planner is None:
        return sim._best_source(req, node_id)
    return planner(sim, req, node_id, isolated=isolated)
