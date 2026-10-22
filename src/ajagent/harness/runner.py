"""Execution management around the existing agent loop, never a second loop."""
from dataclasses import asdict
import json
import time
from pathlib import Path
from ajagent.context.builder import estimate
from ajagent.core.loop import run_agent
from ajagent.core.types import RunResult, ToolCall, ToolResult
from ajagent.tools.registry import execute
from ajagent.tools.base import ToolError
from ajagent.harness.budget import Budget, BudgetExceeded
from ajagent.harness.events import Events, redactor
from ajagent.harness.hooks import Hooks
from ajagent.harness.policy import Policy
from ajagent.harness.state import RunState


class ObservedModel:
    def __init__(self, model, runner):
        self.model, self.runner = model, runner

    def tool_output(self, result, call_id):
        return self.model.tool_output(result, call_id)

    def generate(self, context, tools):
        runner = self.runner
        reserve = getattr(getattr(self.model, 'settings', None), 'output_tokens', 0)
        # 1. Check the budget
        runner.budget.check(estimate([context, [t.definition() for t in tools]]) + reserve)
        started = time.monotonic()
        # 2. Record the model start event
        runner.events.emit('model_started', runner.step, purpose='summary' if runner.building_context else 'agent')
        settings = getattr(self.model, 'settings', None)
        old_timeout = getattr(settings, 'timeout', None)
        old_budget = getattr(self.model, 'request_budget', None)
        if old_timeout is not None:
            settings.timeout = min(old_timeout, runner.budget.remaining_seconds())
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
            if old_timeout is not None:
                settings.timeout = old_timeout
        # 4. Record the model finish event and update the budget usage
        for key in runner.used:
            runner.used[key] += turn.usage.get(key, 0)
        runner.budget.tokens = sum(runner.used.values())
        if not runner.building_context:
            runner.last_usage = dict(turn.usage) or None
        runner.events.emit('model_finished', runner.step, elapsed=time.monotonic() - started,
                           usage=turn.usage, result_summary=turn.text[:500])
        runner.budget.check()
        return turn


class Runner:
    def __init__(self, model, registry, session, builder, execution, *, ask=None,
                 budget=None, hooks=None, secrets=(), denied=(), supplement=None, loop=run_agent):
        self.model, self.registry, self.session, self.builder = model, registry, session, builder
        self.execution = execution
        self.clean = redactor(secrets)
        session.clean = self.clean
        self.events = Events(session.path.parent.parent, self.clean)
        self.policy = Policy(session, ask, denied)
        self.hooks = hooks or Hooks(execution.workspace)
        self.budget = budget or Budget()
        self.supplement, self.loop = supplement, loop
        self.state, self.step, self.repairs = RunState(), 0, 0
        self.completed_step = 0
        self.building_context = False       # summary or agent context
        self.pending = []
        self.used = {'input_tokens': 0, 'output_tokens': 0}
        self.last_usage = None
        self.needs_permission = False
        self.previous_run = session.run_id
        from ajagent.tools.base import ToolSpec, schema
        self.registry['read_artifact'] = ToolSpec('read_artifact', 'Read a bounded slice of a saved run artifact.', schema({
            'path': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 10000000},
            'length': {'type': 'integer', 'minimum': 1, 'maximum': 2000}}, ['path']), self.read_artifact)

    def read_artifact(self, path, offset=0, length=2000):
        target = Path(path)
        if target.is_symlink() or not target.resolve().is_relative_to(self.events.root.parent.resolve()) or target.parent.name != 'artifacts':
            raise ToolError('forbidden', 'Only run artifacts can be read here')
        with target.open(encoding='utf-8') as stream:
            stream.read(offset)
            text = stream.read(length)
        return {'text': text, 'offset': offset, 'next_offset': offset + len(text)}, [{'path': str(target)}]

    def save(self):
        self.events.checkpoint(session_id=self.session.id, state=asdict(self.state),
                               completed_step=self.completed_step, pending=self.pending,
                               changed=sorted(self.hooks.changed),
                               groups=len(self.session.groups), usage=self.session.usage)

    def before_step(self, step):
        self.step = step
        self.building_context = True
        self.budget.check()
        return self.supplement(step) if self.supplement else None

    def context_ready(self, previous_covered):
        self.building_context = False
        if self.builder.covered > previous_covered:
            self.events.emit('compacted', self.step, covered=self.builder.covered,
                               estimated=self.builder.stats)

    def batch_finished(self):
        # The loop flushes the complete replay batch before clearing pending effects.
        self.pending.clear()
        self.completed_step = self.step
        self.save()

    def execute(self, call):
        self.budget.tool()
        started = time.monotonic()
        try:
            logged_args = json.loads(call.arguments)
        except (ValueError, TypeError):
            logged_args = '<invalid JSON>'
        self.events.emit('tool_started', self.step, call.call_id, tool=call.name, arguments=logged_args)
        tool = self.registry.get(call.name)
        args = {}
        try:
            if tool is None:
                result = execute(call, self.registry)
            else:
                from jsonschema import Draft202012Validator
                from ajagent.tools.registry import reject_constant
                args = json.loads(call.arguments, parse_constant=reject_constant)
                if not Draft202012Validator(tool.input_schema).is_valid(args):
                    raise ToolError('invalid_arguments', 'Arguments must match tool schema')
                messages = self.hooks.before(call, args)
                if messages:
                    raise ToolError('hook_denied', '; '.join(messages))
                description = {'tool': call.name, 'arguments': args}
                if call.name == 'write_file':
                    description = self.execution.preview(**args)
                if tool.effect != 'read' and self.policy.key(tool, args) not in self.policy.grants:
                    self.events.emit('permission_requested', self.step, call.call_id, tool=call.name,
                                       summary=description)
                    self.state.status = 'waiting_for_user'
                    self.save()
                decision = self.policy.decide(tool, args, self.clean(description))
                self.state.status = 'running'
                self.events.emit('permission_decided', self.step, call.call_id, tool=call.name, decision=decision)
                if decision != 'allow':
                    self.needs_permission |= decision == 'ask'
                    raise ToolError('permission_required' if decision == 'ask' else 'permission_denied',
                                    'Tool needs explicit approval' if decision == 'ask' else 'Tool denied by user or policy')
                if tool.effect != 'read':
                    self.pending.append({'call_id': call.call_id, 'tool': call.name,
                                         'arguments': self.clean(args), 'status': 'unknown'})
                    self.save()  # Persist intent BEFORE performing the side effect.
                self.execution.timeout = min(self.execution.workspace.timeout, self.budget.remaining_seconds())
                # execute the tool call and return the result
                result = execute(call, self.registry)
                self.hooks.after(call, args, result)
        except ToolError as error:
            result = ToolResult(call.call_id, False, error={'code': error.code, 'message': error.message})
        except (ValueError, TypeError):
            result = ToolResult(call.call_id, False, error={'code': 'invalid_arguments', 'message': 'Invalid tool arguments'})
        except OSError:
            result = ToolResult(call.call_id, False, error={'code': 'io_error', 'message': 'Tool IO failed'})
        result = ToolResult(**self.clean(asdict(result)))
        encoded = json.dumps(asdict(result), ensure_ascii=False)
        if len(encoded) > 6000:
            path = self.events.artifact(asdict(result))
            result.data = {'preview': encoded[:1500], 'artifact': str(path),
                           'hint': 'Use read_artifact to read a bounded slice', 'truncated': True}
        self.events.emit('tool_finished', self.step, call.call_id, tool=call.name,
                           elapsed=time.monotonic() - started, ok=result.ok,
                           result_summary=str(result.data if result.ok else result.error)[:2000])
        return result

    def before_finish(self, answer):
        def validate(argv):
            call = ToolCall(f'validation-{self.repairs}', 'run_command', json.dumps({'argv': argv}))
            result = self.execute(call)
            # Validation has no model protocol items; persist its result before checkpointing.
            self.session.append({'event': 'validation', 'result': asdict(result)})
            self.session.flush()
            self.batch_finished()
            return result
        errors = self.hooks.validate(answer, validate)
        self.events.emit('validation_finished', self.step, errors=errors)
        if self.needs_permission:
            return 'permission_required', errors
        if not errors:
            return None, []
        if self.repairs >= 2:
            return 'validation_failed', errors
        self.repairs += 1
        return None, errors

    def run(self, prompt, *, max_steps=8, max_output_chars=6000, on_tool=None, system_prompt='', acknowledge_unknown=False):
        baseline = dict(self.session.usage)
        if self.previous_run:
            previous = self.events.root.parent / self.previous_run / 'checkpoint.json'
            if previous.exists():
                checkpoint = json.loads(previous.read_text())
                self.hooks.changed.update(checkpoint.get('changed', []))
                if checkpoint.get('pending') and not acknowledge_unknown:
                    self.state.finish('unknown_effect')
                    self.pending = checkpoint['pending']
                    self.save()
                    self.events.emit('run_finished', reason='unknown_effect', state=self.state.status,
                                       previous_run=self.previous_run)
                    return RunResult('unknown_effect', f'Previous side effect has an unknown result. Inspect {previous} and the workspace, then use --acknowledge-unknown.', 0, baseline)
                if checkpoint.get('pending'):
                    self.events.emit('unknown_effect_acknowledged', previous_run=self.previous_run)
        self.session.run_id = self.events.id
        self.session.append({'event': 'harness_run', 'run_id': self.events.id})
        self.session.flush()
        self.save()
        try:
            result = self.loop(prompt, ObservedModel(self.model, self), self.registry, max_steps,
                               max_output_chars, on_tool, system_prompt=system_prompt,
                               session=self.session, context_builder=self.builder, harness=self)
        except (BudgetExceeded, KeyboardInterrupt) as error:
            reason = 'cancelled' if isinstance(error, KeyboardInterrupt) else 'budget_limit'
            total = {key: baseline[key] + self.used[key] for key in baseline}
            self.session.usage = total
            result = RunResult(reason, 'Run cancelled' if reason == 'cancelled' else str(error), self.step, total, self.last_usage)
            self.session.append({'event': 'end', 'status': reason, 'usage': total, 'last_usage': self.last_usage})
        except Exception:
            total = {key: baseline[key] + self.used[key] for key in baseline}
            self.session.usage = total
            result = RunResult('harness_error', 'Execution failed; inspect the run checkpoint before resuming.', self.step, total, self.last_usage)
            self.session.append({'event': 'end', 'status': result.status, 'usage': total, 'last_usage': self.last_usage})
        finally:
            self.session.flush()
        self.state.finish(result.status)
        self.save()
        self.events.emit('run_finished', self.step, state=self.state.status, reason=result.status,
                           usage=result.usage, last_usage=result.last_usage,
                           elapsed=time.monotonic() - self.budget.started)
        return result
