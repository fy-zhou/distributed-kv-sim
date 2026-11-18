"""Read-only research instrumentation and reproducible standard-library I/O."""
import csv
import hashlib
import json
import platform
import statistics
import subprocess
import time
from pathlib import Path

from kv_sim import Simulator
from kv_sim.cache import BlockCache


def write_csv(path, rows):
    rows = list(rows)
    if not rows:
        return
    with Path(path).open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def provenance(config):
    root = Path(__file__).resolve().parents[1]
    paths = sorted(list((root/'kv_sim').rglob('*.py')) + list((root/'scripts').glob('research*.py')))
    return dict(code_commit=subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], universal_newlines=True).strip(),
        source_sha256={str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        python=platform.python_version(), config=config,
        synthetic=True, note='Configured fluid network and illustrative compute; no hardware speedup claim.')


def new_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    return path


def archive(source, destination, names, description):
    """Copy compact evidence, identify every raw file by SHA-256, never overwrite."""
    import shutil
    source, destination = Path(source), new_directory(destination)
    manifest = json.loads((source / 'config.json').read_text())
    manifest['raw_sha256'] = {
        str(p.relative_to(source)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source.rglob('*')) if p.is_file()}
    for name in names:
        shutil.copyfile(str(source / name), str(destination / name))
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (destination / 'README.md').write_text(description + '\n')
    size = sum(p.stat().st_size for p in destination.rglob('*') if p.is_file())
    if size > 1024**2:
        raise ValueError('archive exceeds 1 MiB: %d' % size)


class RecordingCache(BlockCache):
    """Observe actual insertion return values before the next job starts."""
    def admit(self, keys, *, remote=False):
        job = self.owner.nodes[self.node_id].active
        before_blocks = len(self.blocks)
        before_evictions, before_rejections = self.evictions, self.rejections
        if job is not None and self.owner.audit:
            keys = tuple(keys)
            before_digest = digest(list(self.blocks))
            before_prefix = self.prefix_len(job.req.hashes)
        inserted = super().admit(keys, remote=remote)
        if job is not None and self.owner.audit:
            rejected = self.rejections-before_rejections
            self.owner.admissions.append(dict(request_id=job.req.request_id,
                node=self.node_id, time_ms=self.owner.now, remote_blocks=job.remote_blocks,
                proposed=len(keys), inserted=inserted, rejected=rejected,
                touched=len(keys)-inserted-rejected,
                evicted=self.evictions-before_evictions,
                before_digest=before_digest, after_digest=digest(list(self.blocks)),
                request_prefix_before=before_prefix,
                request_prefix_after=self.prefix_len(job.req.hashes)))
            assert inserted == len(self.blocks)-before_blocks+self.evictions-before_evictions
        return inserted


class AuditSimulator(Simulator):
    """Observations stay outside simulation state; interventions are explicit.

    Decision wall time excludes audit collection and CSV serialization. Trace
    overhead is not representative of a production scheduler deployment.
    """
    def __init__(self, specs, config, hot=(), audit=True, allow_remote=True,
                 force=None):
        super().__init__(specs, config)
        self.hot = tuple(hot)
        self.audit = audit
        self.allow_remote = allow_remote
        self.force = force or {}
        self.forecasts = {}
        self.admissions = []
        self.snapshots = []
        self.residuals = []
        self.decision_ns = []
        self.execution_plans = []
        for node_id, node in self.nodes.items():
            node.cache = RecordingCache(node.spec.cache_capacity_blocks, self.cfg.admission)
            node.cache.owner, node.cache.node_id = self, node_id

    def _best_source(self, req, dest):
        if not self.allow_remote:
            local = self._hit(req, dest)
            return local, None, local
        return super()._best_source(req, dest)

    def _snapshot(self, event, request_id):
        if not self.audit:
            return
        for i, n in sorted(self.nodes.items()):
            self.snapshots.append(dict(time_ms=self.now, event=event,
                request_id=request_id, node=i,
                active='' if n.active is None else n.active.req.request_id,
                queued=len(n.queue), remaining_active_ms=self._remaining_active_ms(n),
                hot_contiguous=n.cache.prefix_len(self.hot),
                hot_physical=sum(k in n.cache.blocks for k in self.hot),
                hot_pinned=sum(k in n.cache.pins for k in self.hot),
                pin_references=sum(n.cache.pins.values()),
                evictions=n.cache.evictions, rejections=n.cache.rejections,
                blocks=len(n.cache.blocks),
                cache_digest=digest(list(n.cache.blocks))))

    def _choose(self, req):
        start = time.perf_counter_ns()
        chosen = super()._choose(req)
        self.decision_ns.append(time.perf_counter_ns() - start)
        chosen = self.force.get(req.request_id, chosen)
        if self.audit:
            self.forecasts[req.request_id] = self._estimate_breakdown(req, chosen)
            self._snapshot('decision', req.request_id)
        return chosen

    def _start(self, node_id):
        node = self.nodes[node_id]
        starts = node.active is None and bool(node.queue)
        super()._start(node_id)
        if starts and self.audit:
            job = node.active
            self.execution_plans.append(dict(request_id=job.req.request_id,
                start_ms=self.now, node=node_id, source=job.source,
                local_blocks=job.local_hit, hit_blocks=job.hit,
                remote_blocks=job.remote_blocks))
            self._snapshot('start', job.req.request_id)

    def _on_job_done(self, node_id):
        node = self.nodes[node_id]
        job = node.active
        if self.audit:
            self._snapshot('before_admit', job.req.request_id)
        super()._on_job_done(node_id)
        if self.audit:
            row = self.rows[-1]
            forecast = self.forecasts[job.req.request_id]
            self.residuals.append(dict(request_id=job.req.request_id,
                predicted_node=forecast['node'],
                predicted_remote_blocks=forecast['remote_blocks'],
                actual_remote_blocks=job.remote_blocks,
                predicted_queue_ms=forecast['queue_ms'], actual_queue_ms=row['queue_ms'],
                queue_residual_ms=row['queue_ms']-forecast['queue_ms'],
                predicted_ttft_ms=forecast['estimated_ms'], actual_ttft_ms=row['ttft_ms'],
                ttft_residual_ms=row['ttft_ms']-forecast['estimated_ms']))
            self._snapshot('after_completion_and_next_start', job.req.request_id)

    def save_traces(self, path):
        path = new_directory(path)
        for name, rows in (('requests', self.rows), ('decisions', self.decisions),
                           ('admissions', self.admissions), ('snapshots', self.snapshots),
                           ('residuals', self.residuals), ('execution_plans', self.execution_plans)):
            write_csv(path / (name + '.csv'), rows)
        (path / 'final_caches.json').write_text(json.dumps(
            {i: list(n.cache.blocks) for i, n in self.nodes.items()}, indent=2)+'\n')


def metrics(sim):
    rows = sim.rows
    result = sim.summary()
    result.update(remote_requests=sum(r['remote_hit_tokens'] > 0 for r in rows),
        local_requests=sum(r['local_hit_tokens'] > 0 for r in rows),
        remote_reuse_rate=sum(r['remote_hit_tokens'] > 0 for r in rows)/len(rows),
        local_request_hit_rate=sum(r['local_hit_tokens'] > 0 for r in rows)/len(rows),
        local_token_hit_ratio=sum(r['local_hit_tokens'] for r in rows)/sum(r['length_tokens'] for r in rows),
        remote_token_hit_ratio=sum(r['remote_hit_tokens'] for r in rows)/sum(r['length_tokens'] for r in rows),
        source_hot_final=sim.nodes[0].cache.prefix_len(sim.hot),
        decision_mean_us=statistics.mean(sim.decision_ns)/1000,
        decision_max_us=max(sim.decision_ns)/1000)
    for key in ('queue_ms', 'network_ms', 'h2d_ms', 'compute_ms'):
        result['mean_' + key] = statistics.mean(r[key] for r in rows)
    result['node_requests'] = json.dumps({i: sum(r['node'] == i for r in rows) for i in sim.nodes}, sort_keys=True)
    result['node_service_ms'] = json.dumps({i: sum(r['ttft_ms']-r['queue_ms'] for r in rows if r['node'] == i) for i in sim.nodes}, sort_keys=True)
    result['node_evictions'] = json.dumps({i: n.cache.evictions for i, n in sim.nodes.items()}, sort_keys=True)
    result['node_hot_final'] = json.dumps({i: n.cache.prefix_len(sim.hot) for i, n in sim.nodes.items()}, sort_keys=True)
    return result


def assert_invariants(sim):
    for row in sim.rows:
        assert abs(row['ttft_ms']-sum(row[k] for k in ('queue_ms','network_ms','h2d_ms','compute_ms'))) < 1e-6
        assert row['recomputed_tokens'] + row['total_hit_tokens'] == row['length_tokens']
    assert sim.network.total_bytes == sum(r['remote_hit_tokens'] for r in sim.rows) * sim.cfg.kv_bytes_per_token
    assert not sim.network.flows
    for n in sim.nodes.values():
        assert n.active is None and not n.queue and not n.cache.pins
