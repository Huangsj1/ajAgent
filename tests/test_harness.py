"""Exercise the harness with scripted models and real bounded tools."""
import json
import time
import pytest
from ajagent.context.builder import ContextBuilder
from ajagent.core.types import ModelTurn
from ajagent.harness.budget import Budget
from ajagent.harness.hooks import Hooks
from ajagent.harness.runner import Runner
from ajagent.harness.trace import FakeModel
from ajagent.memory.session import Session
from ajagent.tools.execution.runtime import Execution, command
from ajagent.tools.files.workspace import Workspace
from ajagent.tools.common.base import ToolError, ToolSpec, schema


def call(name, args, id='c'):
    arguments = json.dumps(args)
    return {'tool_calls': [{'call_id': id, 'name': name, 'arguments': arguments}],
            'continuation_items': [{'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': id, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}]}],
            'usage': {'input_tokens': 10, 'output_tokens': 2}}


def final(text='Done'):
    return {'text': text, 'continuation_items': [{'role': 'assistant', 'content': text}],
            'usage': {'input_tokens': 20, 'output_tokens': 3}}


def runner(tmp_path, turns, **kwargs):
    ws = Workspace(tmp_path, tmp_path / 'config.local.toml', timeout=2)
    execution = Execution(ws)
    tools = {t.name: t for t in [*ws.specs(), *execution.specs()]}
    session = kwargs.pop('session', None) or Session(tmp_path / '.ajagent', 'deepseek', 'fake')
    run = Runner(FakeModel(turns), tools, session, ContextBuilder(), execution, **kwargs)
    run.registry['read_artifact'] = run.artifact_tool()
    return run


def events(run):
    return [json.loads(line) for line in (run.events.root / 'events.jsonl').read_text().splitlines()]


def test_read_write_verify_revise(tmp_path):
    (tmp_path / 'evidence.md').write_text('Evidence')
    choices = []
    def approve(description):
        choices.append(description)
        return 'session'
    run = runner(tmp_path, [call('read_file', {'path': 'evidence.md'}),
        call('write_file', {'path': 'answer.md', 'content': '[source](missing.md)'}), final(),
        call('write_file', {'path': 'answer.md', 'content': '[source](evidence.md)'}), final()], ask=approve)
    result = run.run('Write a cited answer')
    assert result.status == 'completed' and run.repairs == 1
    assert len(choices) == 1 and 'diff' in choices[0]
    assert 'evidence.md' in (tmp_path / 'answer.md').read_text()
    names = [e['event'] for e in events(run)]
    assert {'model_started', 'model_finished', 'tool_started', 'tool_finished',
            'permission_requested', 'validation_finished', 'run_finished'} <= set(names)
    assert run.state.status == 'completed'
    assert json.loads((run.events.root / 'checkpoint.json').read_text())['pending'] == []


def test_validation_command_runs_and_repairs_code(tmp_path):
    (tmp_path / 'check.py').write_text('from demo import value\nassert value == 2\n')
    run = runner(tmp_path, [call('write_file', {'path': 'demo.py', 'content': 'value = 1\n'}), final(),
        call('write_file', {'path': 'demo.py', 'content': 'value = 2\n# fixed\n'}), final()], ask=lambda _: 'session')
    run.hooks.validation_command = ['python3', '-B', 'check.py']
    assert run.run('Fix demo').status == 'completed'
    assert run.repairs == 1
    assert len([e for e in events(run) if e['event'] == 'tool_finished' and e['tool'] == 'run_command']) == 2


@pytest.mark.parametrize('ask,status', [(None, 'permission_required'), (lambda _: 'deny', 'completed')])
def test_permission_denial_and_missing_interaction(tmp_path, ask, status):
    run = runner(tmp_path, [call('write_file', {'path': 'no.txt', 'content': 'x'}), final('Cannot write')], ask=ask)
    assert run.run('write').status == status
    assert not (tmp_path / 'no.txt').exists()
    assert run.state.status == ('waiting_for_user' if ask is None else 'completed')
    assert any('permission_' in str(e.get('result_summary', '')) for e in events(run))


def test_hooks_and_validation_repair_limit(tmp_path):
    run = runner(tmp_path, [final(), final(), final(), final()])
    run.hooks.before_finish.append(lambda _: 'Still invalid')
    assert run.run('finish').status == 'validation_failed'
    assert run.repairs == 2
    denied = runner(tmp_path, [call('write_file', {'path': 'no.txt', 'content': 'x'}), final()], ask=lambda _: 'once')
    denied.hooks.before_tool.append(lambda call, args: 'Blocked by hook')
    assert denied.run('write').status == 'completed'
    assert not (tmp_path / 'no.txt').exists()


@pytest.mark.parametrize('budget', [Budget(max_tokens=1), Budget(max_tool_calls=1)])
def test_budget_limits(tmp_path, budget):
    run = runner(tmp_path, [call('list_files', {}), call('list_files', {}, 'd'), final()], budget=budget)
    assert run.run('test').status == 'budget_limit'
    assert run.state.status == 'failed'


def test_wall_clock_step_limit_and_supplement(tmp_path):
    budget = Budget(max_seconds=1)
    budget.started -= 2
    run = runner(tmp_path, [final()], budget=budget)
    assert run.run('test').status == 'budget_limit'
    run = runner(tmp_path, [call('list_files', {})], supplement=lambda _: 'User correction')
    assert run.run('test', max_steps=1).status == 'step_limit'
    assert 'User correction' in run.session.path.read_text()


def test_large_artifact_and_redaction(tmp_path):
    secret = 'secret-api-value'
    run = runner(tmp_path, [call('large', {}), final(secret)], secrets=[secret])
    run.registry['large'] = ToolSpec('large', 'data', schema({}, []), lambda: (secret + 'x' * 10000, []))
    result = run.run('test ' + secret)
    paths = list((run.events.root / 'artifacts').glob('*.json'))
    assert len(paths) == 1
    for path in [run.session.path, *run.events.root.rglob('*.json*')]:
        assert secret not in path.read_text()
    data, _ = run.read_artifact(str(paths[0]), 0, 100)
    assert '[REDACTED]' in data['text']
    assert 'artifact' in run.session.path.read_text()
    assert result.status == 'completed'


def test_command_timeout_output_bound_and_no_shell(tmp_path):
    data, _ = command(['python3', '-c', 'print("x" * 20000)'], tmp_path, output_limit=100)
    assert len(data['output']) == 100 and data['truncated']
    with pytest.raises(ToolError, match='timed out'):
        command(['python3', '-c', 'import time; time.sleep(5)'], tmp_path, timeout=.05)
    with pytest.raises(ToolError, match='Allowed executables'):
        command(['sh', '-c', 'echo no'], tmp_path)


def test_cancelled_state_and_usage(tmp_path):
    run = runner(tmp_path, [])
    class Cancelled:
        def generate(self, context, tools):
            raise KeyboardInterrupt()
    run.model = Cancelled()
    assert run.run('test').status == 'cancelled'
    assert run.state.status == 'cancelled'
    assert events(run)[-1]['event'] == 'run_finished'


def test_offline_cases_and_trace_replay(tmp_path, monkeypatch, capsys):
    import sys
    from pathlib import Path
    from ajagent.harness.trace import main
    cases = [json.loads(line) for line in Path('evals/cases/harness.jsonl').read_text().splitlines()]
    for case in cases:
        run = runner(tmp_path, case['turns'], denied=case.get('deny', []))
        assert run.run(case['prompt']).status == case['expected']
        assert not (tmp_path / 'demo.txt').exists()
        # Replay only reads trace rows; there is no executor or model dependency.
        monkeypatch.setattr('ajagent.harness.runner.invoke', lambda *a: pytest.fail('No tool execution during replay'))
        monkeypatch.setattr(sys, 'argv', ['ajagent-trace', str(run.events.root), '--replay'])
        assert main() == 0
        assert 'run_finished' in capsys.readouterr().out
        monkeypatch.undo()


def test_command_interrupt_reaps_process(tmp_path, monkeypatch):
    import subprocess
    import selectors
    import ajagent.tools.execution.runtime as execution
    original = subprocess.Popen
    processes = []
    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    def interrupt(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(execution.subprocess, 'Popen', spawn)
    monkeypatch.setattr(selectors.KqueueSelector if hasattr(selectors, 'KqueueSelector') else selectors.EpollSelector,
                        'select', interrupt)
    with pytest.raises(KeyboardInterrupt):
        command(['python3', '-c', 'import time; time.sleep(5)'], tmp_path)
    assert len(processes) == 1 and processes[0].poll() is not None


def test_post_response_token_limit_prevents_side_effect(tmp_path):
    run = runner(tmp_path, [], budget=Budget(max_tokens=5000), ask=lambda _: 'once')
    class Expensive:
        def generate(self, context, tools):
            return ModelTurn(usage={'input_tokens': 6000, 'output_tokens': 100})
    run.model = Expensive()
    result = run.run('test')
    assert result.status == 'budget_limit'
    assert result.usage == {'input_tokens': 6000, 'output_tokens': 100}
    assert run.budget.tool_calls == 0


def test_compaction_event_and_total_usage(tmp_path):
    run = runner(tmp_path, [final()])
    # Small injected builder exercises the same observable compaction boundary.
    class Builder:
        usage = {'input_tokens': 0, 'output_tokens': 0}
        covered = 0
        summary = ''
        stats = {}
        def build(self, system, groups, model, tools):
            summary = model.generate([{'role': 'user', 'content': 'summarize'}], [])
            self.usage = summary.usage
            self.covered = 1
            self.summary = summary.text
            return [{'role': 'user', 'content': 'continue'}]
    run.builder = Builder()
    run.model = FakeModel([final('Summary'), final('Answer')])
    result = run.run('test')
    assert result.usage == {'input_tokens': 40, 'output_tokens': 6}
    assert result.last_usage == {'input_tokens': 20, 'output_tokens': 3}
    assert any(e['event'] == 'compacted' for e in events(run))


def test_allow_all_covers_different_tools_and_survives_resume(tmp_path):
    prompts = []
    def approve(description):
        prompts.append(description)
        return 'allow_all'
    run = runner(tmp_path, [
        call('write_file', {'path': 'a.py', 'content': 'print(42)\n'}, 'a'),
        call('write_file', {'path': 'b.txt', 'content': 'hello'}, 'b'),
        call('run_python', {'path': 'a.py'}, 'c'), final()], ask=approve)
    assert run.run('Create and execute').status == 'completed'
    assert len(prompts) == 1
    assert sum(e['event'] == 'permission_requested' for e in events(run)) == 1
    assert all(e['decision'] == 'allow' for e in events(run)
               if e['event'] == 'permission_decided')
    run.session.flush()
    resumed = Session(tmp_path / '.ajagent', 'deepseek', 'fake', run.session.id)
    from ajagent.harness.policy import Policy
    write = run.registry['write_file']
    args = {'path': 'new.txt', 'content': 'new'}
    assert Policy(resumed).decide(write, args, {}) == 'allow'
    assert Policy(resumed, denied=['write_file']).decide(write, args, {}) == 'deny'
    fresh = Session(tmp_path / '.ajagent', 'deepseek', 'fake')
    assert Policy(fresh).decide(write, args, {}) == 'ask'


@pytest.mark.parametrize('answer,expected', [('allow_all', 'allow_all'), ('once', 'once'),
                                           ('session', 'session'), ('no', 'deny')])
def test_cli_permission_choices(monkeypatch, answer, expected):
    from ajagent.cli import approve
    monkeypatch.setattr('builtins.input', lambda _: answer)
    assert approve({'tool': 'write_file'}) == expected


def test_model_error_diagnostics_persist_in_run_and_session(tmp_path):
    from ajagent.models.base import ModelError
    class Broken:
        def generate(self, context, tools):
            raise ModelError('HTTP 402: billing unavailable', kind='billing_error',
                             status=402, attempts=1, request_id='req-test')
    run = runner(tmp_path, [])
    run.model = Broken()
    result = run.run('Work')
    assert result.status == 'api_error'
    assert result.answer == 'HTTP 402: billing unavailable'
    event = next(e for e in events(run) if e.get('error') == 'ModelError')
    assert event['error_details']['http_status'] == 402
    session_events = [json.loads(line) for line in run.session.path.read_text().splitlines()]
    failure = next(e for e in session_events if e['event'] == 'model_error')
    assert failure['kind'] == 'billing_error' and failure['request_id'] == 'req-test'
