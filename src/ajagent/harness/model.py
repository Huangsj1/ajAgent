"""Observe model calls and account for usage, without owning HTTP retries."""
import time
from ajagent.context.builder import estimate
from ajagent.options import DEFAULTS


class ObservedModel:
    def __init__(self, model, runner):
        self.model, self.runner = model, runner

    def tool_output(self, result, call_id):
        return self.model.tool_output(result, call_id)

    def generate(self, context, tools):
        runner = self.runner
        reserve = getattr(getattr(self.model, 'settings', None), 'output_tokens', 0)
        # 1. Check the budget
        runner.budget.check(estimate([context, [t.definition() for t in tools]],
                                     getattr(runner.builder, 'options', DEFAULTS.context).estimated_bytes_per_token) + reserve)
        started = time.monotonic()
        # 2. Record the model start event
        runner.events.emit('model_started', runner.step, purpose='summary' if runner.building_context else 'agent')
        old_budget = getattr(self.model, 'request_budget', None)
        self.model.request_budget = runner.budget
        try:
            # 3. Generate the model turn
            turn = self.model.generate(context, tools)
        except BaseException as error:
            runner.events.emit('model_finished', runner.step, error=type(error).__name__,
                               elapsed=time.monotonic() - started)
            raise
        finally:
            self.model.request_budget = old_budget
        # 4. Record the model finish event and update the budget usage
        for key in runner.used:
            runner.used[key] += turn.usage.get(key, 0)
        runner.budget.tokens = sum(runner.used.values())
        if not runner.building_context:
            runner.last_usage = dict(turn.usage) or None
        runner.events.emit('model_finished', runner.step, elapsed=time.monotonic() - started,
                           usage=turn.usage, result_summary=turn.text[:runner.options.model_log_chars])
        runner.budget.check()
        return turn

