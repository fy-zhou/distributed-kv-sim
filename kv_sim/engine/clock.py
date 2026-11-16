"""Monotonic simulated-time clock in milliseconds."""

class SimulationClock:
    def __init__(self):
        self.now_ms = 0.0

    def advance(self, now_ms):
        if now_ms < self.now_ms - 1e-8:
            raise ValueError("simulation time cannot go backwards")
        self.now_ms = now_ms
