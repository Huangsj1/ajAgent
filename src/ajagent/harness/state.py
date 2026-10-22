"""Execution state is separate from the detailed stop reason."""
from dataclasses import dataclass


@dataclass
class RunState:
    status: str = 'running'
    reason: str | None = None

    def finish(self, reason):
        self.reason = reason
        self.status = ('completed' if reason == 'completed' else
                       'cancelled' if reason == 'cancelled' else
                       'waiting_for_user' if reason in {'permission_required', 'unknown_effect'} else 'failed')
