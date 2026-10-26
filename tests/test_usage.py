"""Usage distinguishes accumulated cost from the last model request."""
from ajagent.context.builder import ContextBuilder
from ajagent.core.loop import run_agent
from ajagent.core.types import ModelTurn, ToolCall
from ajagent.models.base import ModelError


class Model:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def generate(self, context, tools):
        self.calls += 1
        if self.calls == 1:
            return ModelTurn(tool_calls=[ToolCall('c', 'missing', '{}')],
                             usage={'input_tokens': 100, 'output_tokens': 10})
        if self.fail:
            raise ModelError('failed')
        return ModelTurn(text='done', usage={'input_tokens': 180, 'output_tokens': 20})

    def tool_output(self, result, call_id):
        return {'role': 'tool', 'content': result, 'tool_call_id': call_id}


def test_total_and_last_request():
    result = run_agent('test', Model(), {})
    assert result.usage == {'input_tokens': 280, 'output_tokens': 30}
    assert result.last_usage == {'input_tokens': 180, 'output_tokens': 20}


def test_failed_request_retains_last_reported_usage():
    result = run_agent('test', Model(fail=True), {})
    assert result.status == 'api_error'
    assert result.last_usage == result.usage == {'input_tokens': 100, 'output_tokens': 10}


def test_summary_usage_is_counted_once_per_run():
    class Builder(ContextBuilder):
        def __init__(self):
            super().__init__()
            self.usage = {'input_tokens': 1000, 'output_tokens': 100}

        def build(self, system, groups, model, tools):
            self.usage = {k: v + 5 for k, v in self.usage.items()}
            return []
    result = run_agent('test', Model(), {}, context_builder=Builder())
    assert result.usage == {'input_tokens': 290, 'output_tokens': 40}
    assert result.last_usage == {'input_tokens': 180, 'output_tokens': 20}


def test_session_usage_survives_resume_and_accumulates_once(tmp_path):
    import json
    from ajagent.memory.session import Session
    session = Session(tmp_path, 'deepseek', 'test')
    first = run_agent('first', Model(), {}, session=session)
    assert first.usage == {'input_tokens': 280, 'output_tokens': 30}
    resumed = Session(tmp_path, 'deepseek', 'test', session.id)
    second = run_agent('second', Model(), {}, session=resumed)
    assert second.usage == {'input_tokens': 560, 'output_tokens': 60}
    assert second.last_usage == {'input_tokens': 180, 'output_tokens': 20}
    again = Session(tmp_path, 'deepseek', 'test', session.id)
    assert again.usage == second.usage  # Load the last snapshot; do not sum snapshots.
    third = run_agent('third', Model(fail=True), {}, session=again)
    assert third.usage == {'input_tokens': 660, 'output_tokens': 70}
    assert third.last_usage == {'input_tokens': 100, 'output_tokens': 10}
    # Reusing the same object also accumulates, without mutating previous results.
    fourth = run_agent('fourth', Model(), {}, session=again)
    assert fourth.usage == {'input_tokens': 940, 'output_tokens': 100}
    assert third.usage == {'input_tokens': 660, 'output_tokens': 70}
    ends = [json.loads(line) for line in session.path.read_text().splitlines()
            if json.loads(line)['event'] == 'end']
    assert len(ends) == 4 and ends[-1]['usage'] == fourth.usage
    fresh = Session(tmp_path, 'deepseek', 'test')
    assert fresh.usage == {'input_tokens': 0, 'output_tokens': 0}


def test_resume_without_end_defaults_to_zero(tmp_path):
    from ajagent.memory.session import Session
    session = Session(tmp_path, 'deepseek', 'test')
    session.record([{'role': 'user', 'content': 'interrupted'}])
    resumed = Session(tmp_path, 'deepseek', 'test', session.id)
    assert resumed.usage == {'input_tokens': 0, 'output_tokens': 0}


def test_session_total_includes_new_summary_usage(tmp_path):
    from ajagent.memory.session import Session
    class Builder(ContextBuilder):
        usage = {'input_tokens': 0, 'output_tokens': 0}
        stats = {}
        summary = ''
        def build(self, system, groups, model, tools):
            self.usage = {k: v + 5 for k, v in self.usage.items()}
            return []
    session = Session(tmp_path, 'deepseek', 'test')
    run_agent('first', Model(), {}, session=session)
    resumed = Session(tmp_path, 'deepseek', 'test', session.id)
    result = run_agent('second', Model(), {}, session=resumed, context_builder=Builder())
    assert result.usage == {'input_tokens': 570, 'output_tokens': 70}
    assert result.last_usage == {'input_tokens': 180, 'output_tokens': 20}
