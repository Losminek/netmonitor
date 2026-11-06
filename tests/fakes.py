"""Testovací náhrady pingeru."""
import threading
import time

from netmonitor.core.ping_engine import Pinger, PingResult, PingStatus


class FakePinger(Pinger):
    """Chování podle adresy: 'ok:<ms>', 'timeout', 'slow:<s>', 'unreach' nebo callable."""
    name = "fake"

    def __init__(self, behaviour: dict | None = None, default="ok:5"):
        self.behaviour = behaviour or {}
        self.default = default
        self.calls: dict[str, int] = {}
        self.inflight: dict[str, int] = {}
        self.max_inflight: dict[str, int] = {}
        self._lock = threading.Lock()

    def ping(self, address: str, timeout: float) -> PingResult:
        with self._lock:
            self.calls[address] = self.calls.get(address, 0) + 1
            self.inflight[address] = self.inflight.get(address, 0) + 1
            self.max_inflight[address] = max(self.max_inflight.get(address, 0), self.inflight[address])
        try:
            b = self.behaviour.get(address, self.default)
            if callable(b):
                b = b()
            if b.startswith("ok:"):
                return PingResult(PingStatus.OK, rtt_ms=float(b[3:]), address=address)
            if b.startswith("slow:"):
                time.sleep(min(float(b[5:]), timeout))
                return PingResult(PingStatus.TIMEOUT, address=address)
            if b == "unreach":
                return PingResult(PingStatus.HOST_UNREACHABLE, address=address)
            time.sleep(min(timeout, 0.05))
            return PingResult(PingStatus.TIMEOUT, address=address)
        finally:
            with self._lock:
                self.inflight[address] -= 1
