"""Budget phases guide work without granting extra resources or tool authority."""
import pytest
from ajagent.harness.budget import Budget, ChildBudget, BudgetExceeded
from ajagent.harness.trace import FakeModel
from ajagent.options import DEFAULTS, Options
from test_harness import runner, call, final, events


def test_phases_monotonic_and_parent_pressure():
    parent = Budget(max_tokens=10000)
    parent.configure(DEFAULTS.harness, 10)
    assert parent.advance(1) == 'HIGH_BUDGET'
    assert parent.advance(7) == 'LOW_BUDGET'
    assert parent.advance(2) == 'LOW_BUDGET'
    child = ChildBudget(parent, 10, 10000, 30)
    assert child.advance() == 'LOW_BUDGET'
    assert parent.advance(10) == 'FINALIZING'
    assert child.advance() == 'FINALIZING'


def test_request_reservations_are_shared_and_released():
    parent = Budget(max_tokens=100)
    child = ChildBudget(parent, 10, 100, 30)
    with child.request(70):
        assert parent.remaining_tokens() == child.remaining_tokens() == 30
        with pytest.raises(BudgetExceeded):
            with parent.request(31):
                pass
    assert parent.remaining_tokens() == 100


def test_last_step_is_tool_free_and_counts_usage(tmp_path):
    seen = []
    class Recording(FakeModel):
        def generate(self, context, tools):
            seen.append((context, tools))
            return super().generate(context, tools)
    run = runner(tmp_path, [])
    run.model = Recording([call('list_files', {}), final('Inspected; implementation unfinished.')])
    result = run.run('Implement a CLI', max_steps=2)
    assert result.status == 'step_limit'
    assert result.usage == {'input_tokens': 30, 'output_tokens': 5}
    assert seen[0][1] and seen[1][1] == []
    assert 'FINALIZING' in seen[1][0][0]['content']
    assert any(e['event'] == 'budget_finalizing' for e in events(run))


def test_final_tool_call_is_never_executed(tmp_path):
    run = runner(tmp_path, [call('write_file', {'path': 'bad', 'content': 'bad'})], ask=lambda _: 'allow_all')
    result = run.run('Finish', max_steps=1)
    assert result.status == 'step_limit'
    assert not (tmp_path / 'bad').exists()
    assert run.budget.tool_calls == 0


def test_alternating_repeats_force_final_handoff(tmp_path):
    turns = [call('list_files', {}), call('search_files', {'query': 'missing'})] * 2 + [call('list_files', {})]
    turns.append(final('No new evidence; unfinished.'))
    run = runner(tmp_path, turns)
    result = run.run('Inspect')
    assert result.status == 'no_progress'
    assert 'No new evidence' in result.answer
    assert run.budget.phase == 'FINALIZING'


def test_hard_limit_does_not_buy_a_final_call(tmp_path):
    model = FakeModel([final('Must not run')])
    run = runner(tmp_path, [], budget=Budget(max_tokens=1))
    run.model = model
    assert run.run('Work').status == 'budget_limit'
    assert next(model.turns)['text'] == 'Must not run'


def test_low_budget_system_prompt(tmp_path):
    run = runner(tmp_path, [final()])
    run.budget.tokens = int(run.budget.max_tokens * .7)
    class Recording(FakeModel):
        def generate(self, context, tools):
            assert 'LOW_BUDGET' in context[0]['content']
            return super().generate(context, tools)
    run.model = Recording([final()])
    assert run.run('Answer').status == 'completed'


@pytest.mark.parametrize('values', [{'low_budget_ratio': 1.0}, {'finalizing_ratio': .5}, {'repeat_window': 0}])
def test_invalid_phase_configuration(values):
    with pytest.raises(ValueError):
        Options.from_config({'harness': values})


def test_oversized_batch_records_denials_before_finalizing(tmp_path):
    first = call('write_file', {'path': 'one.txt', 'content': 'one'}, 'one')
    second = call('write_file', {'path': 'two.txt', 'content': 'two'}, 'two')
    first['tool_calls'] += second['tool_calls']
    first['continuation_items'][0]['tool_calls'] += second['continuation_items'][0]['tool_calls']
    run = runner(tmp_path, [first, final('Only one file written.')],
                 budget=Budget(max_tool_calls=1), ask=lambda _: 'allow_all')
    result = run.run('Write two files')
    assert result.status == 'budget_limit'
    assert (tmp_path / 'one.txt').exists() and not (tmp_path / 'two.txt').exists()
    assert run.pending == []
    assert run.budget.tool_calls == 1
    assert any('budget_limit' in str(group) for group in run.session.groups)


def test_final_context_excludes_plans_and_caps_output(tmp_path):
    import json
    turn = call('list_files', {})
    turn['continuation_items'][0]['content'] = 'I created phantom.md (unverified plan)'
    class Recording(FakeModel):
        def generate(self, context, tools):
            if not tools:
                assert 'phantom.md' not in json.dumps(context)
                assert self.request_output_tokens == DEFAULTS.harness.final_output_tokens
            return super().generate(context, tools)
    run = runner(tmp_path, [])
    run.model = Recording([turn, final('Only inspected files.')])
    assert run.run('Implement', max_steps=2).status == 'step_limit'
    assert run.model.request_output_tokens is None


def test_request_headroom_triggers_early_handoff():
    budget = Budget(max_tokens=10000)
    assert budget.advance(request_tokens=6000) == 'FINALIZING'
    assert budget.tokens == 0 and budget.reserved == 0


def test_steps_do_not_finalize_at_ninety_percent():
    budget = Budget()
    budget.configure(DEFAULTS.harness, 100)
    assert budget.advance(90) == 'LOW_BUDGET'
    assert budget.snapshot()['steps_remaining'] == 10
    assert budget.advance(99) == 'LOW_BUDGET'
    assert budget.advance(100) == 'FINALIZING'
    assert budget.phase_reason == 'steps'


def test_non_step_trigger_is_identified():
    budget = Budget(max_tokens=10000)
    budget.configure(DEFAULTS.harness, 300)
    budget.tokens = 9100
    assert budget.advance(2) == 'FINALIZING'
    assert budget.phase_reason == 'tokens'


def test_missing_executable_is_actionable(tmp_path, monkeypatch):
    from ajagent.tools.execution.runtime import command
    from ajagent.tools.common.base import ToolError
    monkeypatch.setattr('ajagent.tools.execution.runtime.shutil.which', lambda _: None)
    with pytest.raises(ToolError, match='not on PATH') as error:
        command(['rg', 'needle', '.'], tmp_path)
    assert error.value.code == 'executable_not_found'
