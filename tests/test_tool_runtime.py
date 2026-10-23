"""Unified tool selection, demand-loaded skills and concurrency boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
from threading import Barrier, Event, Lock
import time
import httpx
import pytest
from ajagent.config import Settings
from ajagent.core.types import ToolCall
from ajagent.harness.budget import Budget, BudgetExceeded, ChildBudget
from ajagent.tools.common.base import ToolSpec, schema
from ajagent.tools.runtime.config import ToolConfig
from ajagent.tools.runtime.loader import ToolManager
from ajagent.tools.delegation.agent import delegation_tool
from ajagent.tools.delegation.proposals import Proposals, proposal_tools, digest
from ajagent.harness.trace import FakeModel
from test_harness import runner, call, final
from test_delegation import contract


def test_unified_selection_and_skills(tmp_path):
    skill = tmp_path / 'skills' / 'research'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: research\ndescription: Find evidence\n---\nBODY_NOT_AT_START\n')
    settings = Settings('deepseek', 'fake', 'fake', 'https://example.test', tmp_path, tmp_path / 'config.local.toml')
    run = runner(tmp_path, [])
    with httpx.Client() as client, ToolManager(settings, {'tools': {'enabled': ['files', 'skills'], 'disabled': ['search_files']}}, run, client) as manager:
        assert set(run.registry) == {'read_file', 'list_files', 'load_skill'}
        assert 'Find evidence' in manager.instructions and 'BODY_NOT_AT_START' not in manager.instructions
        result = run.execute(ToolCall('load', 'load_skill', '{"name":"research"}'))
        assert result.ok and 'BODY_NOT_AT_START' in result.data['instructions']
        assert set(run.registry) == {'read_file', 'list_files', 'load_skill'}
    for config in ({'tools': {'enabled': ['typo']}}, {'tools': {'enabled': True}}):
        with pytest.raises(ValueError):
            ToolConfig.load(config)


def test_parallel_reads_ordered_results_and_write_barrier(tmp_path):
    run = runner(tmp_path, [])
    barrier = Barrier(2)
    completed, written = set(), []
    lock = Lock()
    def read(n):
        barrier.wait(timeout=3)
        with lock:
            completed.add(n)
        return {'n': n}, []
    def write():
        assert completed == {1, 2}
        written.append(True)
        return 'written', []
    def later():
        assert written
        return 'later', []
    run.policy.ask = lambda _: 'once'
    run.registry.update(read=ToolSpec('read', '', schema({'n': {'type': 'integer'}}, ['n']), read),
                        write=ToolSpec('write', '', schema({}, []), write, 'execute'),
                        later=ToolSpec('later', '', schema({}, []), later))
    calls = [ToolCall('a', 'read', '{"n":1}'), ToolCall('b', 'read', '{"n":2}'),
             ToolCall('c', 'write', '{}'), ToolCall('d', 'later', '{}')]
    results = list(run.execute_many(calls))
    assert all(result.ok for result in results)
    assert [result.call_id for result in results] == ['a', 'b', 'c', 'd']
    assert [result.data for result in results] == [{'n': 1}, {'n': 2}, 'written', 'later']


def test_shared_model_budget_reservations_prevent_overcommit():
    parent = Budget(max_tokens=100)
    a, b = ChildBudget(parent, 10, 100, 30), ChildBudget(parent, 10, 100, 30)
    with a.request(70):
        with pytest.raises(BudgetExceeded):
            with b.request(40):
                pytest.fail('overcommitted')
        assert parent.reserved == 70
    with b.request(100):
        assert parent.reserved == 100
    assert parent.reserved == a.reserved == b.reserved == 0


def test_parallel_children_keep_model_budget_and_context_isolated(tmp_path):
    run = runner(tmp_path, [])
    barrier = Barrier(2)
    seen = []
    lock = Lock()
    def model_factory():
        model = FakeModel([final('report')])
        generate = model.generate
        def observed(context, tools):
            with lock:
                seen.append((str(context), model.request_budget))
            barrier.wait(timeout=3)
            return generate(context, tools)
        model.generate = observed
        return model
    run.registry['delegate'] = delegation_tool(run, model_factory)
    calls = [ToolCall(str(i), 'delegate', json.dumps({'contract': contract(f'task_{i}')})) for i in range(2)]
    results = list(run.execute_many(calls))
    assert all(result.ok and result.data['status'] == 'completed' for result in results)
    assert len({id(budget) for _, budget in seen}) == 2
    assert all(('task_0' in text) != ('task_1' in text) for text, _ in seen)
    assert run.used == {'input_tokens': 40, 'output_tokens': 6}
    assert run.budget.reserved == 0


def test_single_writer_proposals_conflict_and_syntax(tmp_path):
    path = tmp_path / 'demo.py'
    path.write_text('value = 1\n')
    run = runner(tmp_path, [], ask=lambda _: 'once')
    run.registry.update({tool.name: tool for tool in proposal_tools(run)})
    child = runner(tmp_path, [])
    child.parent = run
    proposals = Proposals(child, contract('change demo'))
    original_hash = digest(path.read_bytes())
    first = proposals.propose('demo.py', original_hash, 'value = 2\n', 'increase')[0]['artifact']
    second = proposals.propose('demo.py', original_hash, 'value = 3\n', 'alternate')[0]['artifact']
    assert path.read_text() == 'value = 1\n'
    result = run.execute(ToolCall('apply', 'apply_proposal', json.dumps({'artifact': first, 'review': 'Reviewed behavior and syntax'})))
    assert result.ok and path.read_text() == 'value = 2\n'
    assert 'demo.py' in run.hooks.changed
    conflict = run.execute(ToolCall('apply2', 'apply_proposal', json.dumps({'artifact': second, 'review': 'Review alternative'})))
    assert not conflict.ok and conflict.error['code'] == 'proposal_conflict'
    from ajagent.tools.common.base import ToolError
    with pytest.raises(ToolError, match='invalid'):
        proposals.propose('demo.py', digest(path.read_bytes()), 'def broken(', 'bad')
    limited = Proposals(child, {**contract('limited'), 'allowed_paths': ['other.py']})
    with pytest.raises(ToolError, match='outside'):
        limited.propose('demo.py', digest(path.read_bytes()), 'value = 4\n', 'wrong scope')


def test_mcp_local_credentials_are_not_file_tool_inputs(tmp_path):
    (tmp_path / 'mcp.local.toml').write_text('secret')
    run = runner(tmp_path, [])
    result = run.execute(ToolCall('read', 'read_file', '{"path":"mcp.local.toml"}'))
    assert not result.ok and result.error['code'] == 'forbidden'


def test_invalid_contract_never_starts_child(tmp_path):
    run = runner(tmp_path, [])
    def forbidden():
        pytest.fail('Invalid contracts must fail before constructing a child')
    run.registry['delegate'] = delegation_tool(run, forbidden)
    result = run.execute(ToolCall('bad', 'delegate', json.dumps({'contract': {**contract('task'), 'acceptance_criteria': []}})))
    assert not result.ok and result.error['code'] == 'invalid_arguments'


def test_read_failure_does_not_discard_other_results(tmp_path):
    run = runner(tmp_path, [])
    from ajagent.tools.common.base import ToolError
    def fail():
        raise ToolError('unavailable', 'fixture unavailable')
    run.registry['fail'] = ToolSpec('fail', '', schema({}, []), fail)
    results = list(run.execute_many([ToolCall('bad', 'fail', '{}'), ToolCall('good', 'list_files', '{}')]))
    assert not results[0].ok and results[1].ok


def test_proposal_requires_approval(tmp_path):
    run = runner(tmp_path, [])
    run.registry.update({tool.name: tool for tool in proposal_tools(run)})
    child = runner(tmp_path, [])
    child.parent = run
    proposal = Proposals(child, contract('new file')).propose('new.py', digest(b''), 'value = 1\n', 'new module')[0]['artifact']
    result = run.execute(ToolCall('apply', 'apply_proposal', json.dumps({'artifact': proposal, 'review': 'Reviewed'})))
    assert not result.ok and result.error['code'] == 'permission_required'
    assert not (tmp_path / 'new.py').exists()
