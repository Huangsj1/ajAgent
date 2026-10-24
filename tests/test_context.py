"""Budgeted context keeps protocol batches and immutable raw history."""
from copy import deepcopy
import pytest
from ajagent.context.builder import ContextBuilder, estimate
from ajagent.context.compaction import ContextError
from ajagent.core.types import ModelTurn
from ajagent.models.base import ModelError


class Summary:
    def __init__(self, failure=False):
        self.calls = 0
        self.failure = failure

    def generate(self, context, tools):
        self.calls += 1
        assert not tools
        assert 'Evidence locations' in context[0]['content']
        if self.failure:
            raise ModelError('unavailable')
        return ModelTurn(text='Goal: fix. Constraints: keep API. Completed work: read. Open questions: test. Evidence locations: a.py:12')


@pytest.mark.parametrize('provider', ['deepseek', 'openai'])
def test_truncate_without_mutating_history(provider):
    if provider == 'deepseek':
        batch = [{'role': 'assistant', 'tool_calls': [{'id': 'c', 'type': 'function', 'function': {'name': 'read', 'arguments': '{}'}}]},
                 {'role': 'tool', 'tool_call_id': 'c', 'content': 'x' * 10000}]
        field = 'content'
    else:
        batch = [{'type': 'reasoning', 'encrypted_content': 'opaque'},
                 {'type': 'function_call', 'call_id': 'c', 'name': 'read', 'arguments': '{}'},
                 {'type': 'function_call_output', 'call_id': 'c', 'output': 'x' * 10000}]
        field = 'output'
    groups = [[{'role': 'user', 'content': 'Inspect a.py'}], batch]
    original = deepcopy(groups)
    builder = ContextBuilder(2000, 500)
    result = builder.build('system', groups, Summary(), [])
    assert groups == original
    assert len(result[-1][field]) < 1200
    assert result[-2] == batch[-2]
    assert builder.stats['estimated'] is True


def history():
    return [[{'role': 'user', 'content': 'Keep exact task and constraints'}],
            [{'role': 'assistant', 'content': 'old notes ' * 150}],
            [{'role': 'assistant', 'content': 'recent ' * 120}],
            [{'role': 'assistant', 'content': 'latest ' * 120}]]


def test_summary_preserves_recent_and_current_task():
    groups = history()
    original = deepcopy(groups)
    builder, model = ContextBuilder(1250, 200), Summary()
    result = builder.build('system', groups, model, [])
    assert model.calls == 1
    assert result[-2:] == groups[-2] + groups[-1]
    assert any(item.get('content') == 'Keep exact task and constraints' for item in result)
    assert 'a.py:12' in str(result)
    assert groups == original and builder.covered == 2
    builder.build('system', groups, model, [])
    assert model.calls == 1


def test_failed_summary_preserves_state():
    groups, model = history(), Summary(failure=True)
    builder = ContextBuilder(1250, 200)
    original = deepcopy(groups)
    with pytest.raises(ContextError):
        builder.build('system', groups, model, [])
    assert model.calls == 1 and builder.covered == 0 and builder.summary == ''
    assert groups == original


def test_oversize_recent_context_stops_without_request():
    model = Summary()
    with pytest.raises(ContextError):
        ContextBuilder(1000, 200).build('x' * 10000, [], model, [])
    assert model.calls == 0


def test_oversized_summary_rolls_back_and_loop_ends(tmp_path):
    from ajagent.core.loop import run_agent
    from ajagent.memory.session import Session
    class Oversized(Summary):
        def generate(self, context, tools):
            self.calls += 1
            return ModelTurn(text='x' * 10000)
    model = Oversized()
    builder = ContextBuilder(1250, 200)
    with pytest.raises(ContextError):
        builder.build('system', history(), model, [])
    assert builder.covered == 0 and builder.summary == '' and model.calls == 1
    session = Session(tmp_path, 'deepseek', 'test')
    result = run_agent('x' * 10000, model, {}, session=session,
                       context_builder=ContextBuilder(1000, 200))
    assert result.status == 'context_error'
    assert 'x' * 10000 in session.path.read_text()
    assert model.calls == 1


def test_summary_can_be_disabled_without_touching_history():
    from dataclasses import replace
    from ajagent.options import DEFAULTS, Options

    builder = ContextBuilder(1000, 100, replace(DEFAULTS.context, summarization_enabled=False))
    groups = [[{'role': 'user', 'content': 'x' * 4000}]]

    class Never:
        def generate(self, *args):
            raise AssertionError('disabled summarization called model')

    with pytest.raises(ContextError, match='disabled'):
        builder.build('system', groups, Never(), [])
    assert len(groups[0][0]['content']) == 4000 and builder.covered == 0
    with pytest.raises(ValueError, match='boolean'):
        Options.from_config({'limits': {'context': {'summarization_enabled': 'false'}}})
