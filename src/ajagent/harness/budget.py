"""Thread-safe hierarchical budgets, including in-flight model reservations."""
from contextlib import contextmanager
from collections import deque
from dataclasses import dataclass
from threading import RLock, Event
import time
from ajagent.options import DEFAULTS


class BudgetExceeded(Exception):
    pass


class FinalizeRequired(Exception):
    """Work must stop while there is still room for a factual handoff."""


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
        self.options = DEFAULTS.harness
        self.phase = 'HIGH_BUDGET'
        self.phase_reason = None
        self.step = 0
        self.max_steps = None
        self.recent_batches = deque(maxlen=self.options.repeat_window)

    def configure(self, options, max_steps):
        self.options, self.max_steps = options, max_steps
        self.recent_batches = deque(self.recent_batches, maxlen=options.repeat_window)

    def observe_batch(self, signature, repeat_limit):
        """Detect unchanged evidence even in alternating tool batches."""
        self.recent_batches.append(signature)
        repeats = self.recent_batches.count(signature)
        if repeats >= repeat_limit:
            self.phase = 'FINALIZING'
            self.phase_reason = 'repeated_batches'
        elif repeats == repeat_limit - 1 and self.phase == 'HIGH_BUDGET':
            self.phase = 'LOW_BUDGET'
        return repeats

    def remaining_tokens(self):
        with self.lock:
            remaining = max(0, self.max_tokens - self.tokens - self.reserved)
            return min(remaining, self.parent.remaining_tokens()) if self.parent else remaining

    def snapshot(self):
        with self.lock:
            tools = self.max_tool_calls - self.tool_calls
            if self.parent:
                tools = min(tools, self.parent.snapshot()['tools_remaining'])
            return {'phase': self.phase, 'phase_reason': self.phase_reason, 'tokens_remaining': self.remaining_tokens(),
                    'tools_remaining': max(0, tools), 'seconds_remaining': self.remaining_seconds(),
                    'steps_remaining': max(0, self.max_steps - self.step) if self.max_steps else None}

    def advance(self, step=None, request_tokens=0):
        """Phases only move forward; descendants also observe parent pressure."""
        with self.lock:
            if step is not None:
                self.step = step
            state = self.snapshot()
            ratios = {'tokens': state['tokens_remaining'] / self.max_tokens,
                      'tools': state['tools_remaining'] / self.max_tool_calls,
                      'time': state['seconds_remaining'] / self.max_seconds}
            if self.max_steps:
                ratios['steps'] = state['steps_remaining'] / self.max_steps
            if self.parent:
                self.parent.advance()
            target, reason = 'HIGH_BUDGET', min(ratios, key=ratios.get)
            if ratios[reason] <= self.options.low_budget_ratio:
                target = 'LOW_BUDGET'
            # Steps are discrete: reserve one final turn, not 10% of all turns.
            final_ratios = {key: value for key, value in ratios.items() if key != 'steps'}
            resource = min(final_ratios, key=final_ratios.get)
            if final_ratios[resource] <= self.options.finalizing_ratio:
                target, reason = 'FINALIZING', resource
            if self.max_steps and state['steps_remaining'] < self.options.final_step_reserve:
                target, reason = 'FINALIZING', 'steps'
            if request_tokens and state['tokens_remaining'] < request_tokens * 2:
                target, reason = 'FINALIZING', 'request_headroom'
            order = ['HIGH_BUDGET', 'LOW_BUDGET', 'FINALIZING']
            if self.parent and order.index(self.parent.phase) > order.index(target):
                target, reason = self.parent.phase, 'parent:' + str(self.parent.phase_reason)
            if order.index(target) > order.index(self.phase):
                self.phase, self.phase_reason = target, reason
            return self.phase

    def prompt(self):
        guidance = {
            'HIGH_BUDGET': 'Identify the minimum requested deliverables. Inspect known relevant paths directly, then implement and verify. Avoid redundant reads, environment checks without a concrete need, and unnecessary delegation.',
            'LOW_BUDGET': 'Stop broad exploration and new subtasks. Prioritize all requested deliverables, including documentation, then essential verification. Reuse existing evidence; do not repeat already successful checks without changes.',
            'FINALIZING': 'Do not call tools. Give a factual handoff: completed changes, observed tests, unfinished work, and the next action. Treat assistant statements as intentions, not evidence. Claim changes and tests only when successful tool outputs support them. Never claim unverified completion.',
        }
        return f"Budget phase: {self.phase}. {guidance[self.phase]}\nRemaining budget: {self.snapshot()}"


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

    def charge(self, usage):
        """Charge actual usage once along the entire ancestor chain."""
        with self.lock:
            self.tokens += sum(usage.get(key, 0) for key in ('input_tokens', 'output_tokens'))
            if self.parent:
                self.parent.charge(usage)

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
