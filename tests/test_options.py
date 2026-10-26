"""Configuration overrides must reach real consumers, not just parse successfully."""
import json
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
import httpx
import numpy as np
import pytest
from ajagent.options import DEFAULTS, Options, read_config
from ajagent.core.types import ToolCall, ToolResult, ModelTurn
from ajagent.tools.files.workspace import Workspace
from ajagent.tools.common.registry import execute, serialize


@pytest.mark.parametrize('config', [
    {'limits': {'unknown': {}}},
    {'limits': {'tools': {'max_file_btyes': 10}}},
    {'limits': {'tools': {'max_file_bytes': True}}},
    {'limits': {'retry': {'attempts': 0}}},
    {'limits': {'retrieval': {'embedding_dimensions': 2048}}},
    {'limits': {'retrieval': {'hnsw_m': 1}}},
    {'limits': {'tools': {'allowed_commands': []}}},
    {'limits': {'tools': {'default_read_lines': 500}}},
    {'harness': {'max_seconds': float('inf')}},
    {'harness': {'artifact_preview_chars': 10000}},
])
def test_invalid_options_fail_early(config):
    with pytest.raises(ValueError):
        Options.from_config(config)


def test_options_are_immutable_and_isolated():
    settings = Options.from_config({'limits': {'memory': {'max_entries': 1}}})
    assert settings.memory.max_entries == 1 and DEFAULTS.memory.max_entries == 20
    with pytest.raises(FrozenInstanceError):
        settings.memory.max_entries = 2


def test_file_limits_and_schema_share_config(tmp_path):
    opts = Options.from_config({'limits': {'tools': {'max_file_bytes': 4, 'max_list_files': 1,
                                                    'max_read_lines': 2, 'default_read_lines': 1}}})
    (tmp_path / 'a.txt').write_text('abcde')
    (tmp_path / 'b.txt').write_text('a\nb')
    workspace = Workspace(tmp_path, tmp_path / 'local.toml', options=opts.tools)
    registry = {t.name: t for t in workspace.specs()}
    result = execute(ToolCall('c', 'read_file', '{"path":"a.txt"}'), registry)
    assert result.error['code'] == 'too_large'
    result = execute(ToolCall('c', 'read_file', '{"path":"b.txt","max_lines":3}'), registry)
    assert result.error['code'] == 'invalid_arguments'
    data, _ = workspace.read_file('b.txt')
    assert len(data['lines']) == 1
    data, _ = workspace.list_files()
    assert len(data['files']) == 1 and data['truncated']


def test_memory_and_session_buffer_use_options(tmp_path):
    from ajagent.memory.long_term import Memory
    from ajagent.memory.session import Session
    opts = Options.from_config({'limits': {'memory': {'max_entries': 1, 'text_chars': 8,
                                                     'session_buffer_bytes': 1}}})
    memory = Memory(tmp_path, opts.memory)
    memory.remember('small')
    with pytest.raises(ValueError):
        memory.remember('second')
    with pytest.raises(ValueError):
        memory.remember('too long text')
    session = Session(tmp_path, 'deepseek', 'fake', options=opts.memory)
    assert session.path.exists() and not session._pending


def test_context_excerpt_and_estimation_options():
    from ajagent.context.builder import ContextBuilder
    opts = replace(DEFAULTS.context, tool_excerpt_chars=40, estimated_bytes_per_token=2)
    groups = [[{'role': 'user', 'content': 'read'}],
              [{'role': 'assistant', 'tool_calls': [{'id': 'c'}]}, {'role': 'tool', 'tool_call_id': 'c', 'content': 'x' * 2000}]]
    builder = ContextBuilder(400, 100, opts)
    context = builder.build('system', groups, None, [])
    assert context[-1]['content'].startswith('x' * 40 + '\n')
    assert len(groups[-1][-1]['content']) == 2000
    assert builder.stats['input_tokens'] <= builder.budget


def test_retry_options_control_actual_requests():
    from ajagent.models.base import HTTPModel, ModelError
    options = Options.from_config({'limits': {'retry': {'attempts': 2, 'backoff_seconds': .01}}})
    requests, sleeps = [], []
    def handler(request):
        requests.append(request)
        return httpx.Response(500)
    settings = SimpleNamespace(base_url='https://example.org', api_key='fake', timeout=1, options=options)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ModelError, match='2 attempts'):
            HTTPModel(settings, client, sleep=sleeps.append).post('/test', {})
    assert len(requests) == 2 and sleeps == [.01]


def test_retrieval_limits_flow_from_config_to_backend(tmp_path):
    from ajagent.rag.service import RagSettings, build_service
    config = {'rag': {'sources': ['local'], 'rerank': False}, 'limits': {
        'retrieval': {'default_results': 1, 'result_limit': 2, 'rg_batch_files': 1, 'rrf_offset': 10},
        'tools': {'max_file_bytes': 100}}}
    settings = RagSettings.load(tmp_path / 'unused.toml', config=config)
    (tmp_path / 'a.txt').write_text('needle\n')
    (tmp_path / 'b.txt').write_text('needle\n')
    service = build_service(tmp_path, settings)
    result = service.search('needle')
    assert len(result.hits) == 1 and result.hits[0].score == pytest.approx(1 / 11)
    assert service.backends['local'].options.rg_batch_files == 1
    with pytest.raises(ValueError):
        service.search('needle', limit=3)


def test_changed_chunk_configuration_requires_new_index(tmp_path):
    from ajagent.rag.semantic.store import SemanticIndex
    class Encoder:
        dimensions = 2
        fingerprint = 'test'
        def encode(self, texts, query=False):
            return np.tile(np.array([[1, 0]], dtype=np.float32), (len(texts), 1))
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_text('sample ' * 20)
    options = Options.from_config({'limits': {'retrieval': {'chunk_chars': 20}}})
    index = SemanticIndex(source, tmp_path / 'index', Encoder(), options=options)
    assert index.update()['embedded'] > 1
    assert SemanticIndex.open(tmp_path / 'index', Encoder(), options=options).update()['embedded'] == 0
    with pytest.raises(ValueError, match='settings differ'):
        SemanticIndex.open(tmp_path / 'index', Encoder())


def test_embedding_fingerprint_tracks_input_configuration():
    from ajagent.rag.semantic.encoder import QwenEncoder
    default = QwenEncoder()
    configured = QwenEncoder(options=replace(DEFAULTS.retrieval, embedding_tokens=512, embedding_batch=2))
    assert default.fingerprint != configured.fingerprint
    assert QwenEncoder(options=replace(DEFAULTS.retrieval, embedding_batch=2)).fingerprint == default.fingerprint


def test_oversized_error_serialization_terminates_and_preserves_source():
    result = ToolResult('c', False, error={'code': 'bad', 'message': 'x' * 10000})
    text = serialize(result, 1000)
    assert len(text) <= 1000 and json.loads(text)['ok'] is False
    assert len(result.error['message']) == 10000
    artifact = ToolResult('c', True, data={'artifact': '/tmp/artifacts/example.json', 'preview': 'x' * 10000})
    assert json.loads(serialize(artifact, 1000))['data']['artifact'] == '/tmp/artifacts/example.json'


def test_cli_reads_config_once_and_injects_options(tmp_path, monkeypatch):
    import sys
    import ajagent.cli as cli
    import ajagent.config as config_module
    import ajagent.rag.service as rag_module
    config = tmp_path / 'config.toml'
    config.write_text('[deepseek]\napi_key="fake"\nmodel="fake"\n[limits.tools]\nmax_file_bytes=8\n')
    reads = []
    def read(path):
        reads.append(path)
        return read_config(path)
    monkeypatch.setattr(cli, 'read_config', read)
    monkeypatch.setattr(config_module, 'read_config', lambda _: pytest.fail('Duplicate config read'))
    monkeypatch.setattr(rag_module, 'read_config', lambda _: pytest.fail('Duplicate RAG config read'))
    class Model:
        def generate(self, context, tools):
            assert next(t for t in tools if t.name == 'read_file').handler.__self__.options.max_file_bytes == 8
            return ModelTurn(text='ok', continuation_items=[{'role': 'assistant', 'content': 'ok'}])
    monkeypatch.setattr(cli, 'DeepSeekModel', lambda *args: Model())
    monkeypatch.setattr(sys, 'argv', ['ajagent', '--config', str(config), '--workspace', str(tmp_path), 'hello'])
    assert cli.main() == 0 and len(reads) == 1


def test_execution_and_harness_options_are_used(tmp_path):
    from ajagent.tools.execution.runtime import Execution, command
    from ajagent.tools.common.base import ToolError
    from test_harness import runner, final
    options = Options.from_config({'limits': {'tools': {'write_chars': 4, 'command_output_bytes': 12}},
                                   'harness': {'max_repairs': 0}})
    execution = Execution(Workspace(tmp_path, tmp_path / 'config', options=options.tools))
    registry = {t.name: t for t in execution.specs()}
    result = execute(ToolCall('c', 'write_file', '{"path":"a.txt","content":"12345"}'), registry)
    assert result.error['code'] == 'invalid_arguments' and not (tmp_path / 'a.txt').exists()
    data, _ = command(['python3', '-c', 'print("x" * 30)'], tmp_path, options=options.tools)
    assert len(data['output']) == 12 and data['truncated']
    with pytest.raises(ToolError):
        command(['python3', '-c', 'print(1)'], tmp_path, options=replace(options.tools, allowed_commands=('rg',)))
    run = runner(tmp_path, [final()], options=options)
    run.hooks.before_finish.append(lambda _: 'Validation failed')
    assert run.run('test').status == 'validation_failed' and run.repairs == 0
