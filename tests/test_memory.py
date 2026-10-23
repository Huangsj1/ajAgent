"""Cross-session isolation, explicit memory lifecycle and protocol replay."""
import json
import pytest
from ajagent.memory.long_term import Memory
from ajagent.memory.session import Session
from ajagent.core.loop import run_agent
from ajagent.core.types import ModelTurn
from ajagent.context.builder import ContextBuilder


class Echo:
    def generate(self, context, tools):
        self.context = context
        text = json.dumps(context, ensure_ascii=False)
        return ModelTurn(text=text, continuation_items=[{'role': 'assistant', 'content': 'ack'}])


def test_resume_and_new_session(tmp_path):
    first = Session(tmp_path, 'deepseek', 'test')
    run_agent('Remember task code ORION', Echo(), {}, session=first)
    second = Session(tmp_path, 'deepseek', 'test', first.id)
    model = Echo()
    run_agent('Continue', model, {}, session=second)
    assert 'ORION' in json.dumps(model.context)
    fresh = Session(tmp_path, 'deepseek', 'test')
    run_agent('New task', model, {}, session=fresh)
    assert 'ORION' not in json.dumps(model.context)
    with pytest.raises(ValueError):
        Session(tmp_path, 'openai', 'test', first.id)
    with pytest.raises(ValueError):
        Session(tmp_path, 'deepseek', 'test', '../escape')


def test_memory_update_forget_and_input_difference(tmp_path):
    memory = Memory(tmp_path)
    key = memory.remember('Prefer concise Chinese explanations', source='explicit user preference')
    assert key in (tmp_path / 'memory/MEMORY.md').read_text()
    models, sizes = [], []
    for enabled in [False, True]:
        model, builder = Echo(), ContextBuilder()
        run_agent('Explain agents', model, {}, context_builder=builder,
                  system_prompt='Instructions' + (memory.context() if enabled else ''))
        models.append(model)
        sizes.append(builder.stats['input_tokens'])
    assert 'concise Chinese' not in json.dumps(models[0].context)
    assert 'concise Chinese' in json.dumps(models[1].context)
    assert sizes[1] > sizes[0]
    memory.remember('Prefer English', memory_id=key)
    assert len(memory.list()) == 1 and 'Chinese' not in memory.context()
    memory.forget(key)
    assert Memory(tmp_path).context() == ''
    assert key not in (tmp_path / 'memory/MEMORY.md').read_text()


def test_memory_bounds(tmp_path):
    memory = Memory(tmp_path)
    with pytest.raises(ValueError):
        memory.remember('x' * 1001)
    for i in range(20):
        memory.remember(str(i))
    with pytest.raises(ValueError):
        memory.remember('overflow')
    with pytest.raises(ValueError):
        memory.forget('missing')


@pytest.mark.parametrize('provider', ['deepseek', 'openai'])
def test_tool_batch_resume_preserves_protocol_and_full_result(tmp_path, provider):
    from ajagent.core.types import ToolCall
    from ajagent.tools.common.base import ToolSpec
    from ajagent.models.deepseek import DeepSeekModel
    from ajagent.models.openai import OpenAIModel
    call = ToolCall('call-1', 'large', '{}')
    if provider == 'deepseek':
        items = [{'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': call.call_id, 'type': 'function', 'function': {'name': 'large', 'arguments': '{}'}}]}]
        output = DeepSeekModel.tool_output
    else:
        items = [{'type': 'reasoning', 'encrypted_content': 'opaque'},
                 {'type': 'function_call', 'call_id': call.call_id, 'name': 'large', 'arguments': '{}'}]
        output = OpenAIModel.tool_output
    class ToolModel:
        tool_output = output
        def generate(self, context, tools):
            return ModelTurn(tool_calls=[call], continuation_items=items)
    session = Session(tmp_path, provider, 'test')
    tool = ToolSpec('large', 'large output', {'type': 'object', 'properties': {}}, lambda **kwargs: ('z' * 10000, []))
    result = run_agent('read', ToolModel(), {'large': tool}, max_steps=1, session=session)
    assert result.status == 'step_limit'
    loaded = Session(tmp_path, provider, 'test', session.id)
    assert loaded.groups[-1][:-1] == items
    assert 'z' * 10000 in session.path.read_text()
    echo = Echo()
    run_agent('continue', echo, {}, session=loaded, context_builder=ContextBuilder())
    assert items[0] in echo.context


@pytest.mark.parametrize('enabled', [True, False])
def test_cli_instructions_memory_and_checkpoint(tmp_path, monkeypatch, enabled):
    import sys
    import ajagent.cli as cli
    config = tmp_path / 'config.toml'
    config.write_text('[deepseek]\nmodel="test"\napi_key="test-key"\n')
    (tmp_path / 'AJAGENT.md').write_text('Project instruction: cite evidence.')
    Memory(tmp_path / '.ajagent').remember('Preference: short answers')
    session = Session(tmp_path / '.ajagent', 'deepseek', 'test')
    session.record([{'role': 'user', 'content': 'Old task'}])
    session.append({'event': 'context', 'summary': 'Earlier progress', 'summarized_groups': 1})
    session.flush()
    model = Echo()
    monkeypatch.setattr(cli, 'DeepSeekModel', lambda *args: model)
    monkeypatch.setattr(sys, 'argv', ['ajagent', '--config', str(config), '--workspace', str(tmp_path),
        '--resume', session.id, '--memory' if enabled else '--no-memory', 'Continue'])
    assert cli.main() == 0
    assert 'Project instruction: cite evidence.' in str(model.context)
    assert ('Preference: short answers' in str(model.context)) == enabled
    assert 'Earlier progress' in str(model.context)


def test_buffer_flush_and_size_limit(tmp_path):
    session = Session(tmp_path, 'deepseek', 'test')
    assert not session.path.exists()
    event = {'event': 'context', 'summary': 'original'}
    session.append(event)
    event['summary'] = 'changed'
    assert not session.path.exists()
    session.record([{'role': 'user', 'content': 'hello'}])
    assert not session._pending
    assert 'original' in session.path.read_text() and 'changed' not in session.path.read_text()
    session.append({'event': 'large', 'text': 'x' * 65536})
    assert not session._pending and 'x' * 65536 in session.path.read_text()


def test_interrupt_flushes_pending_events(tmp_path):
    class Interrupted:
        def generate(self, context, tools):
            session.append({'event': 'pending', 'text': 'keep me'})
            raise KeyboardInterrupt()
    session = Session(tmp_path, 'deepseek', 'test')
    with pytest.raises(KeyboardInterrupt):
        run_agent('test', Interrupted(), {}, session=session)
    assert 'keep me' in session.path.read_text()
    assert not session._pending
