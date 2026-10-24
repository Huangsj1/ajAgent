"""Observe model calls and account for usage, without owning HTTP retries."""
import time
from ajagent.context.builder import estimate
from ajagent.options import DEFAULTS
from ajagent.models.base import ModelError
from ajagent.harness.budget import FinalizeRequired


class ObservedModel:
    def __init__(self, model, runner):
        self.model, self.runner = model, runner

    def tool_output(self, result, call_id):
        return self.model.tool_output(result, call_id)

    def generate(self, context, tools):
        runner = self.runner
        reserve = getattr(self.model, 'request_output_tokens', None) or getattr(getattr(self.model, 'settings', None), 'output_tokens', 0)
        # 1. Check the budget
        estimate_tokens = estimate([context, [t.definition() for t in tools]],
                                   getattr(runner.builder, 'options', DEFAULTS.context).estimated_bytes_per_token) + reserve
        if not runner.finalizing:
            previous = runner.budget.phase
            phase = runner.budget.advance(request_tokens=estimate_tokens)
            if phase != previous:
                runner.events.emit('budget_phase_changed', runner.step, **runner.budget.snapshot())
            if phase == 'FINALIZING':
                raise FinalizeRequired
            if phase != previous and context and context[0].get('role') == 'system':
                context = [dict(context[0]), *context[1:]]
                context[0]['content'] = context[0]['content'].split('\nBudget phase:')[0] + '\n' + runner.budget.prompt()
                estimate_tokens = estimate([context, [t.definition() for t in tools]],
                                           runner.all_options.context.estimated_bytes_per_token) + reserve
        started = time.monotonic()
        runner.events.emit('model_started', runner.step, purpose='summary' if runner.building_context else 'agent')
        with runner.budget.request(estimate_tokens) as settle:
            old_budget = getattr(self.model, 'request_budget', None)
            self.model.request_budget = runner.budget
            try:
                turn = self.model.generate(context, tools)
                settle(lambda: runner.account_usage(turn.usage))
            except BaseException as error:
                details = runner.clean(error.details) if isinstance(error, ModelError) else None
                if details is not None:
                    runner.session.append({'event': 'model_error', 'step': runner.step, **details})
                    runner.session.flush()
                runner.events.emit('model_finished', runner.step, error=type(error).__name__, error_details=details,
                                   elapsed=time.monotonic() - started)
                raise
            finally:
                self.model.request_budget = old_budget
        if not runner.building_context:
            runner.last_usage = dict(turn.usage) or None
        runner.events.emit('model_finished', runner.step, elapsed=time.monotonic() - started,
                           usage=turn.usage, result_summary=turn.text[:runner.options.model_log_chars])
        runner.budget.check()
        return turn

