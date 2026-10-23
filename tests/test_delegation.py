"""Delegation isolation and accounting using deterministic models."""
from dataclasses import replace
import json
from ajagent.core.types import ModelTurn, ToolCall
from ajagent.harness.budget import Budget
from ajagent.harness.trace import FakeModel
from ajagent.options import DEFAULTS
from ajagent.tools.delegation.agent import delegation_tool
from test_harness import runner, call as raw_call, final, events


def contract(task):
    return {"goal": task, "context": "", "acceptance_criteria": ["Return evidence and gaps"], "allowed_paths": ["."]}


def call(name, args, id="c"):
    if name == "delegate" and "task" in args:
        args = {"contract": contract(args["task"])}
    return raw_call(name, args, id)


def child_result(run):
    records = [json.loads(line) for line in run.session.path.read_text().splitlines()]
    return next(record for record in records if record['event'] == 'tool_result')


def test_child_isolation_handoff_and_cumulative_usage(tmp_path):
    (tmp_path / 'source.txt').write_text('42 observations')
    child = FakeModel([call('read_file', {'path': 'source.txt'}), final('Evidence summary')])
    captured = []
    generate = child.generate
    def observe(context, tools):
        captured.append((context, [tool.name for tool in tools]))
        return generate(context, tools)
    child.generate = observe
    run = runner(tmp_path, [call('delegate', {'task': 'Read source.txt'}), final('Parent answer')])
    run.registry['delegate'] = delegation_tool(run, lambda: child)
    result = run.run('PARENT_PRIVATE_CONTEXT')
    assert result.status == 'completed'
    assert result.usage == {'input_tokens': 60, 'output_tokens': 10}
    assert result.last_usage == {'input_tokens': 20, 'output_tokens': 3}
    assert run.budget.tokens == 70 and run.budget.tool_calls == 2
    assert 'PARENT_PRIVATE_CONTEXT' not in str(captured)
    assert not {'delegate', 'run_python', 'write_file', 'read_artifact'} & set(captured[0][1])
    report = child_result(run)
    assert 'Evidence summary' in str(report) and 'artifacts' in str(report)
    assert run.evidence.values()[0]['uri'] == 'source.txt'
    assert '42 observations' not in str(run.session.groups)  # Child transcript stays separate.
    assert any(event['event'] == 'delegation_finished' for event in events(run))


def test_child_unknown_recursive_tool_and_denied_parent_tool(tmp_path):
    (tmp_path / 'private.txt').write_text('private data')
    child = FakeModel([call('delegate', {'task': 'recurse'}), call('read_file', {'path': 'private.txt'}), final('Blocked')])
    run = runner(tmp_path, [call('delegate', {'task': 'read'}), final()], denied=['read_file'])
    run.registry['delegate'] = delegation_tool(run, lambda: child)
    assert run.run('test').status == 'completed'
    child_logs = [path for path in run.session.path.parent.glob('*.jsonl') if path != run.session.path]
    text = child_logs[0].read_text()
    assert 'unknown_tool' in text and 'permission_denied' in text
    assert 'private data' not in text


def test_child_failure_does_not_abort_parent(tmp_path):
    run = runner(tmp_path, [call('delegate', {'task': 'fail'}), final('Child failed, continuing')])
    run.registry['delegate'] = delegation_tool(run, lambda: FakeModel([]))
    result = run.run('test')
    assert result.status == 'completed' and result.answer == 'Child failed, continuing'
    assert 'harness_error' in str(child_result(run))


def test_child_limit_is_independent_but_charged_to_parent(tmp_path):
    options = replace(DEFAULTS, capabilities=replace(DEFAULTS.capabilities, child_tool_calls=1))
    child = FakeModel([call('list_files', {}), call('list_files', {})])
    run = runner(tmp_path, [call('delegate', {'task': 'list twice'}), final()], options=options)
    run.registry['delegate'] = delegation_tool(run, lambda: child)
    assert run.run('test').status == 'completed'
    assert 'budget_limit' in str(child_result(run))
    assert run.budget.tool_calls == 2
    assert run.used == {'input_tokens': 50, 'output_tokens': 9}


def test_parent_budget_stops_child_before_model_call(tmp_path):
    child = FakeModel([final('Must not run')])
    run = runner(tmp_path, [], budget=Budget(max_tokens=4000))
    run.registry['delegate'] = delegation_tool(run, lambda: child)
    result = run.execute(ToolCall('c', 'delegate', json.dumps({'contract': contract('证' * 7000)})))
    assert result.ok and result.data['status'] == 'budget_limit'
    assert run.used == {'input_tokens': 0, 'output_tokens': 0}
    assert next(child.turns)['text'] == 'Must not run'


def test_child_actual_usage_counts_before_parent_next_request(tmp_path):
    huge = final('child overshoot')
    huge['usage'] = {'input_tokens': 5000, 'output_tokens': 1}
    run = runner(tmp_path, [call('delegate', {'task': 'read'}), final('Must not run')], budget=Budget(max_tokens=4000))
    run.registry['delegate'] = delegation_tool(run, lambda: FakeModel([huge]))
    result = run.run('test')
    assert result.status == 'budget_limit'
    assert result.usage == {'input_tokens': 5010, 'output_tokens': 3}
    assert run.budget.tokens == 5013


def test_resume_keeps_child_usage_and_evidence(tmp_path):
    from ajagent.memory.session import Session
    (tmp_path / 'source.txt').write_text('Evidence')
    run = runner(tmp_path, [call('delegate', {'task': 'read'}), final()])
    run.registry['delegate'] = delegation_tool(run, lambda: FakeModel([
        call('read_file', {'path': 'source.txt'}), final('Summary')]))
    first = run.run('test')
    session = Session(tmp_path / '.ajagent', 'deepseek', 'fake', run.session.id)
    resumed = runner(tmp_path, [final('resumed')], session=session)
    second = resumed.run('continue')
    assert second.usage == {key: value + final()['usage'][key] for key, value in first.usage.items()}
    assert resumed.evidence.values() == run.evidence.values()


def test_child_inherits_tool_hooks(tmp_path):
    (tmp_path / 'blocked.txt').write_text('should not read')
    run = runner(tmp_path, [call('delegate', {'task': 'read'}), final()])
    run.hooks.before_tool.append(lambda call, args: 'Blocked by hook' if call.name == 'read_file' else None)
    run.registry['delegate'] = delegation_tool(run, lambda: FakeModel([
        call('read_file', {'path': 'blocked.txt'}), final('blocked')]))
    assert run.run('test').status == 'completed'
    child_log = next(path for path in run.session.path.parent.glob('*.jsonl') if path != run.session.path)
    assert 'hook_denied' in child_log.read_text() and 'should not read' not in child_log.read_text()


def test_interrupt_during_child_cancels_parent(tmp_path):
    child = FakeModel([])
    def interrupt(*args):
        raise KeyboardInterrupt
    child.generate = interrupt
    run = runner(tmp_path, [call('delegate', {'task': 'read'}), final('Must not continue')])
    run.registry['delegate'] = delegation_tool(run, lambda: child)
    assert run.run('test').status == 'cancelled'
