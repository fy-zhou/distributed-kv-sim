"""Summary statistics for a completed simulation."""
import statistics

def summarize(sim):
    if not sim.rows:
        raise RuntimeError("run the simulator first")
    timings = sorted(row["ttft_ms"] for row in sim.rows)
    def pct(p):
        pos = (len(timings) - 1) * p
        i = int(pos)
        return (timings[i] * (1-(pos-i)) +
                timings[min(i+1,len(timings)-1)] * (pos-i))
    total_tokens = sum(r["length_tokens"] for r in sim.rows)
    hits = sum(r["total_hit_tokens"] for r in sim.rows)
    makespan = max(r["finished_ms"] for r in sim.rows)
    return {
        "policy": sim.cfg.policy,
        "admission": sim.cfg.admission,
        "requests": len(sim.rows),
        "mean_ttft_ms": statistics.mean(timings),
        "p50_ttft_ms": pct(.5),
        "p95_ttft_ms": pct(.95),
        "p99_ttft_ms": pct(.99),
        "makespan_ms": makespan,
        "throughput_req_s": len(sim.rows)*1000/max(makespan,1e-9),
        "hit_ratio": hits/total_tokens,
        "network_gib": sim.network.total_bytes/2**30,
        "evictions": sum(n.cache.evictions for n in sim.nodes.values()),
        "rejections": sum(n.cache.rejections for n in sim.nodes.values()),
    }
