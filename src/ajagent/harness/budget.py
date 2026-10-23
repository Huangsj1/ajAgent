"""Thread-safe hierarchical budgets, including in-flight model reservations."""
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock, Event
import time
from ajagent.options import DEFAULTS


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_tool_calls: int = DEFAULTS.harness.max_tool_calls
    max_tokens: int = DEFAULTS.harness.max_tokens
    max_seconds: float = DEFAULTS.harness.max_seconds

    def __post_init__(self):
        import math
        if self.max_tool_calls < 1 or self.max_tokens < 1 or not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError('Budget limits must be positive and finite')
        self.started = time.monotonic()
        self.tool_calls = self.tokens = self.reserved = 0
        self.lock, self.cancelled = RLock(), Event()
        self.parent = None

    def check(self, tokens=0):
        with self.lock:
            if self.cancelled.is_set():
                raise KeyboardInterrupt
            if time.monotonic() - self.started >= self.max_seconds:
                raise BudgetExceeded('Wall-clock limit reached')
            if self.tokens + self.reserved + tokens > self.max_tokens:
                raise BudgetExceeded('Token budget reached')
            if self.parent:
                self.parent.check(tokens)

    def tool(self):
        with self.lock:
            self.check()
            if self.tool_calls >= self.max_tool_calls:
                raise BudgetExceeded('Tool-call limit reached')
            if self.parent:
                self.parent.tool()
            self.tool_calls += 1

    def _reserve(self, amount):
        self.reserved += amount
        if self.parent:
            self.parent._reserve(amount)

    @contextmanager
    def request(self, estimate):
        with self.lock:
            self.check(estimate)
            self._reserve(estimate)
        active = True
        def settle(account):
            nonlocal active
            with self.lock:
                if not active:
                    raise RuntimeError('Request already settled')
                account()
                self._reserve(-estimate)
                active = False
        try:
            yield settle
        finally:
            with self.lock:
                if active:
                    self._reserve(-estimate)

    def cancel(self):
        self.cancelled.set()

    def remaining_seconds(self):
        # An active request already owns its reservation; only time/cancel apply here.
        if self.cancelled.is_set():
            raise KeyboardInterrupt
        remaining = self.max_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded('Wall-clock limit reached')
        if self.parent:
            remaining = min(remaining, self.parent.remaining_seconds())
        return remaining


class ChildBudget(Budget):
    def __init__(self, parent, max_tool_calls, max_tokens, max_seconds):
        super().__init__(max_tool_calls, max_tokens, max_seconds)
        self.parent = parent
        self.lock, self.cancelled = parent.lock, parent.cancelled
