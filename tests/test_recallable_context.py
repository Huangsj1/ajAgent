"""Behavioral checks for recall, immutable episodes and speculative commits."""
from copy import deepcopy
from dataclasses import replace
from threading import Event, Thread
import json
import pytest
from ajagent.context.builder import ContextBuilder, estimate
from ajagent.context.compaction import ContextError, SECTIONS
from ajagent.core.types import ModelTurn
from ajagent.memory.session import Session
from ajagent.options import DEFAULTS, Options
from ajagent.tools.history.recall import History
from ajagent.tools.common.base import ToolError


class Summarizer:
    def __init__(self, gate=None, invalid=False):
        self.gate, self.invalid = gate, invalid
        self.started = Event()
        self.inputs = []

    def generate(self, context, tools):
        source = json.loads(context[1]['content'])
        self.inputs.append(source)
        self.started.set()
        if self.gate:
            assert self.gate.wait(5), 'test failed to release compaction'
        state = {section: [] for section in SECTIONS}
        state['decisions'] = [{'text': 'Use the documented API',
                               'refs': ['g99999' if self.invalid else source[0]['id']]}]
        return ModelTurn(text=json.dumps(state), usage={'input_tokens': 30, 'output_tokens': 10})


def groups():
    return [[{'role': 'user', 'content': 'Keep exact constraints'}],
            [{'role': 'assistant', 'content': 'legacy marker SECRET_DETAIL_42 ' + 'old ' * 1600}],
            [{'role': 'assistant', 'content': 'recent ' * 100}],
            [{'role': 'assistant', 'content': 'tail ' * 100}]]


def builder_for(history, *, soft=True):
    size = estimate([[{'role': 'system', 'content': 'system'}] + [item for g in history for item in g], []])
    # Exact thresholds are exercised without wall-clock sleeps.
    budget = int(size / (.83 if soft else .96))
    return ContextBuilder(budget + 100, 100)


def test_soft_is_nonblocking_and_waits_for_hard_threshold():
    history, gate = groups(), Event()
    builder, model = builder_for(history), Summarizer(gate)
    try:
        result = builder.build('system', history, model, [])
        assert model.started.wait(1)
        assert builder.covered == 0 and result[-1] == history[-1][0]
        gate.set()
        builder._pending[1].result(timeout=2)
        builder.poll('system', history, [], wait=True)
        builder.build('system', history, model, [])
        assert builder.covered == 0 and builder._pending is not None
        history.append([{'role': 'assistant', 'content': 'new ' * 900}])
        builder.build('system', history, model, [])
        assert builder.covered == 2
        assert builder.usage == {'input_tokens': 30, 'output_tokens': 10}
    finally:
        gate.set()
        builder.close()


def test_stale_result_discarded_even_when_history_length_is_unchanged():
    history, gate = groups(), Event()
    builder, model = builder_for(history), Summarizer(gate)
    try:
        builder.build('system', history, model, [])
        assert model.started.wait(1)
        history[0][0]['content'] = 'Corrected requirement'
        gate.set()
        builder.poll('system', history, [], wait=True, adopt=True)
        assert builder.covered == 0 and not builder.episodes
        assert 'stale_discarded' in builder.diagnostics
        assert builder.usage['input_tokens'] == 30
    finally:
        gate.set()
        builder.close()


def test_hard_waits_and_reuses_proposal_after_tail_append():
    history, gate = groups(), Event()
    builder, model = builder_for(history), Summarizer(gate)
    builder.build('system', history, model, [])
    assert model.started.wait(1)
    history.append([{'role': 'assistant', 'content': 'new ' * 900}])
    done, errors = Event(), []
    def build():
        try:
            builder.build('system', history, model, [])
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()
    worker = Thread(target=build)
    worker.start()
    assert not done.wait(.05)
    gate.set()
    worker.join(5)
    builder.close()
    assert done.is_set() and not errors
    assert 'stale_discarded' not in builder.diagnostics and builder.covered == 2
    assert builder.usage['input_tokens'] == 30
    assert history[-1][0]['content'] == 'new ' * 900


def test_multiple_passes_never_resummarize_frozen_episodes_and_recall_survives_resume(tmp_path):
    history = groups()
    session = Session(tmp_path, 'deepseek', 'fake')
    for group in history:
        session.record(group)
    builder, model = builder_for(history, soft=False), Summarizer()
    builder.bind(session)
    builder.build('system', history, model, [])
    frozen = deepcopy(builder.episodes[0])
    first_end = builder.covered
    for text in ['new ' * 1500, 'next', 'tail']:
        group = [{'role': 'assistant', 'content': text}]
        session.record(group)
        history.append(group)
    builder.build('system', history, model, [])
    builder.close()
    assert builder.episodes[0] == frozen and builder.covered > first_end
    assert all(int(item['id'][1:]) > first_end for batch in model.inputs[1:] for item in batch)
    restored = Session(tmp_path, 'deepseek', 'fake', session.id)
    other = ContextBuilder()
    other.bind(restored)
    assert other.episodes == builder.episodes and other.summary == builder.summary
    recall = History(restored, replace(DEFAULTS.context, history_page_chars=80))
    data, _ = recall.search('SECRET_DETAIL_42')
    assert data['hits'][0]['group_id'] == 'g2'
    pieces, offset = [], 0
    while offset is not None:
        page, _ = recall.read('g2', offset)
        pieces.append(page['text'])
        offset = page['next_offset']
    assert json.loads(''.join(pieces)) == history[1]
    assert restored.groups == history[builder.covered:]
    assert restored.groups.start == builder.covered


def test_invented_refs_rejected_and_charged_without_state_change():
    history, model = groups(), Summarizer(invalid=True)
    builder = builder_for(history, soft=False)
    original = deepcopy(history)
    with pytest.raises(ContextError, match='invented'):
        builder.build('system', history, model, [])
    assert history == original and builder.covered == 0 and not builder.episodes
    assert builder.usage['output_tokens'] == 10


@pytest.mark.parametrize('responses', [False, True])
def test_tool_pairs_are_never_split_and_unbalanced_input_fails(responses):
    call = {'type': 'function_call', 'call_id': 'x', 'name': 'read', 'arguments': '{}'} if responses else {
        'role': 'assistant', 'tool_calls': [{'id': 'x', 'function': {'name': 'read', 'arguments': '{}'}}]}
    result = {'type': 'function_call_output', 'call_id': 'x', 'output': 'observed'} if responses else {
        'role': 'tool', 'tool_call_id': 'x', 'content': 'observed'}
    history = groups()
    history.insert(1, [call, result])
    model, builder = Summarizer(), builder_for(history, soft=False)
    builder.build('system', history, model, [])
    assert any(item['items'] == [call, result] for item in model.inputs[0])
    bad = [[call], [result]]
    with pytest.raises(ContextError, match='Unbalanced'):
        ContextBuilder().build('system', bad, model, [])


def test_recall_scoped_bounded_redacted_and_search_paginated(tmp_path):
    session = Session(tmp_path, 'deepseek', 'fake')
    session.clean = lambda obj: json.loads(json.dumps(obj).replace('private-key', '[REDACTED]'))
    for i in range(3):
        session.record([{'role': 'user', 'content': f'needle {i} private-key'}])
    recall = History(session, replace(DEFAULTS.context, history_search_results=1))
    first, _ = recall.search('needle')
    second, _ = recall.search('needle', first['next_cursor'])
    assert first['hits'][0]['group_id'] == 'g1' and second['hits'][0]['group_id'] == 'g2'
    assert 'private-key' not in str(recall.read('g1'))
    assert not recall.search('missing')[0]['hits']
    with pytest.raises(ToolError):
        recall.read('g999')
    with pytest.raises(ToolError):
        recall.read('../another-session')
    with pytest.raises(ToolError):
        recall.search('')


def test_invalid_thresholds_and_changed_checkpoint_fall_back_to_raw():
    with pytest.raises(ValueError, match='soft_ratio'):
        Options.from_config({'limits': {'context': {'soft_ratio': .9, 'hard_ratio': .8}}})
    history = groups()
    builder = builder_for(history, soft=False)
    builder.build('system', history, Summarizer(), [])
    checkpoint = builder.snapshot(history)
    history[0][0]['content'] = 'Changed'
    restored = ContextBuilder()
    restored.restore(checkpoint, history)
    assert restored.covered == 0 and not restored.episodes


def test_task_handoff_invalidates_worker_and_preserves_recall(tmp_path):
    history, gate = groups(), Event()
    session = Session(tmp_path, 'deepseek', 'fake')
    for group in history:
        session.record(group)
    builder, model = builder_for(history), Summarizer(gate)
    builder.bind(session)
    builder.build('system', history, model, [])
    assert model.started.wait(1)
    builder.handoff('Durable task state: tests pending', history)
    gate.set()
    builder.poll('system', history, [], wait=True, adopt=True)
    builder.close()
    assert builder.summary == 'Durable task state: tests pending'
    assert 'stale_discarded' in builder.diagnostics
    assert History(session).search('SECRET_DETAIL_42')[0]['hits']


def test_summary_accounting_is_isolated_from_main_request(tmp_path):
    from test_harness import runner, final
    from ajagent.harness.model import ObservedModel
    run = runner(tmp_path, [final('summary')])
    run.last_usage = {'input_tokens': 99, 'output_tokens': 7}
    ObservedModel(run.model, run).generate_summary([{'role': 'user', 'content': 'summarize'}], [])
    assert run.last_usage == {'input_tokens': 99, 'output_tokens': 7}
    assert run.used == {'input_tokens': 20, 'output_tokens': 3}
    assert run.budget.reserved == 0


def test_loop_can_recall_compacted_detail_and_finish(tmp_path):
    from ajagent.core.loop import run_agent
    from ajagent.core.types import ToolCall
    from ajagent.models.deepseek import DeepSeekModel
    session = Session(tmp_path, 'deepseek', 'fake')
    for group in groups():
        session.record(group)
    builder = builder_for(session.groups, soft=False)
    builder.bind(session)
    builder.build('system', session.groups, Summarizer(), [])
    builder.budget = 8000
    recall = History(session)
    class Agent:
        tool_output = DeepSeekModel.tool_output
        def __init__(self):
            self.calls = 0
        def generate(self, context, tools):
            self.calls += 1
            if self.calls == 1:
                assert 'SECRET_DETAIL_42' not in str(context)
                args = json.dumps({'query': 'SECRET_DETAIL_42'})
                return ModelTurn(tool_calls=[ToolCall('h', 'history_search', args)], continuation_items=[{
                    'role': 'assistant', 'tool_calls': [{'id': 'h', 'type': 'function',
                        'function': {'name': 'history_search', 'arguments': args}}]}])
            assert 'SECRET_DETAIL_42' in context[-1]['content']
            return ModelTurn(text='Recovered original detail in g2.', continuation_items=[{
                'role': 'assistant', 'content': 'Recovered original detail in g2.'}])
    result = run_agent('Recall the omitted legacy detail', Agent(), {t.name: t for t in recall.specs()},
                       session=session, context_builder=builder, system_prompt='system')
    assert result.status == 'completed' and 'g2' in result.answer


def test_background_usage_settled_before_session_end(tmp_path):
    from test_harness import runner
    run = runner(tmp_path, [])
    run.registry.clear()
    for group in groups():
        run.session.record(group)
    run.builder = builder_for(run.session.groups)
    gate, started = Event(), Event()
    class Model:
        def generate(self, context, tools):
            if 'Summarize historical data' in context[0]['content']:
                started.set()
                assert gate.wait(3)
                return Summarizer().generate(context, tools)
            assert started.wait(3)
            gate.set()
            return ModelTurn(text='done', usage={'input_tokens': 50, 'output_tokens': 5},
                             continuation_items=[{'role': 'assistant', 'content': 'done'}])
    run.model = Model()
    result = run.run('continue', system_prompt='system')
    assert result.status == 'completed'
    assert result.usage == {'input_tokens': 80, 'output_tokens': 15}
    assert result.last_usage == {'input_tokens': 50, 'output_tokens': 5}
    assert run.budget.reserved == 0
    restored = Session(tmp_path / '.ajagent', 'deepseek', 'fake', run.session.id)
    assert restored.usage == result.usage


def test_uncovered_tail_edit_does_not_invalidate_ready_segment():
    history, model = groups(), Summarizer()
    builder = builder_for(history)
    try:
        builder.build('system', history, model, [])
        episode = builder._pending[1].result(timeout=2)
        assert episode['end'] == 2
        history[3][0]['content'] = 'Corrected recent observation'
        history.append([{'role': 'assistant', 'content': 'new ' * 900}])
        builder.build('system', history, model, [])
        assert builder.covered == 2 and len(model.inputs) == 1
        assert 'stale_discarded' not in builder.diagnostics
        assert 'Corrected recent observation' in str(builder._pack('system', history, []))
    finally:
        builder.close()
