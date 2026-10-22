"""Per-run limits, checked at model/tool boundaries."""
from dataclasses import dataclass
import time


class BudgetExceeded(Exception):
    pass


@dataclass
class Budget:
    max_tool_calls: int = 30
    max_tokens: int = 100000
    max_seconds: float = 300

    def __post_init__(self):
        import math
        if self.max_tool_calls < 1 or self.max_tokens < 1 or not math.isfinite(self.max_seconds) or self.max_seconds <= 0:
            raise ValueError('Budget limits must be positive and finite')
        self.started = time.monotonic()
        self.tool_calls = 0
        self.tokens = 0

    def check(self, tokens=0):
        if time.monotonic() - self.started >= self.max_seconds:
            raise BudgetExceeded('Wall-clock limit reached')
        if self.tokens + tokens > self.max_tokens:
            raise BudgetExceeded('Token budget reached')

    def tool(self):
        self.check()
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded('Tool-call limit reached')
        self.tool_calls += 1

    def remaining_seconds(self):
        self.check()
        return max(.001, self.max_seconds - (time.monotonic() - self.started))
