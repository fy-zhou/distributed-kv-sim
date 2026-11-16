"""Request and job runtime state plus event identifiers."""
from dataclasses import dataclass
from ..cache.prefix_hash import prefix_hashes

@dataclass(frozen=True)
class Request:
    request_id: str
    arrival_ms: float
    hashes: tuple
    length_tokens: int

    @classmethod
    def from_tokens(cls, request_id, arrival_ms, tokens, block_size=16,
                    namespace="qwen3-0.6b"):
        return cls(str(request_id), float(arrival_ms),
                   prefix_hashes(tokens, block_size, namespace), len(tokens))


@dataclass
class Job:
    req: Request
    target: int
    start_ms: float
    queue_ms: float
    local_hit: int
    hit: int
    source: object
    remote_blocks: int
    transfer_ms: float = 0.0
    load_ms: float = 0.0
    compute_ms: float = 0.0
    flow_id: object = None
    expected_finish_ms: object = None
    transfer_start_ms: object = None

EVENT_ARRIVAL = "arrival"
EVENT_FLOW_START = "flow_start"
EVENT_FLOW_DONE = "flow_done"
EVENT_JOB_DONE = "job_done"
