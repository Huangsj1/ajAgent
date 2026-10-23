import json
import subprocess
import sys
import pytest
from ajagent.rag.types import Hit, SearchResult
from ajagent.rag.service import RetrievalService, RagSettings, build_service
from ajagent.tools.retrieval.search import retrieval_tool
from ajagent.core.loop import run_agent
from ajagent.core.types import ModelTurn


class Backend:
    def __init__(self, hits):
        self.hits = hits

    def search(self, *args, **kwargs):
        return SearchResult(self.hits)


class Ranker:
    def score(self, query, texts):
        return [0.9 if 'answer' in text else 0.1 for text in texts]


def test_fusion_rerank_and_partial_failure():
    first = Hit('1', 'local', 'a.md', 'unrelated', start_line=1, end_line=1)
    second = Hit('2', 'semantic', 'b.md', 'answer', start_line=2, end_line=3)
    class Failed:
        def search(self, *args, **kwargs):
            raise TimeoutError()
    service = RetrievalService({'local': Backend([first]), 'semantic': Backend([first, second]),
                                'web': Failed()}, Ranker())
    result = service.search('question')
    assert len(result.hits) == 2
    assert result.hits[0].uri == 'b.md'
    assert set(result.hits[1].metadata['backend_scores']) == {'local', 'semantic'}
    assert result.warnings
    assert first.metadata == {}  # Cached backend results must not mutate.
    with pytest.raises(ValueError):
        service.search('q', ['disabled'])


def test_tool_whole_evidence_budget_and_no_mandatory_retrieval():
    hit = Hit('1', 'local', 'a', 'x' * 3000)
    service = RetrievalService({'local': Backend([hit])})
    tool = retrieval_tool(service, max_chars=1000)
    data, _ = tool.handler('q')
    assert not data['hits'] and data['truncated']
    class DirectModel:
        def generate(self, context, tools):
            return ModelTurn(text='Hello!')
    result = run_agent('hello', DirectModel(), {'retrieve': tool})
    assert result.answer == 'Hello!' and result.status == 'completed'


def test_config_and_lazy_models(tmp_path):
    config = tmp_path / 'config.toml'
    config.write_text('[rag]\nsources=["web"]\nrerank=false\n')
    settings = RagSettings.load(config, sources=['local'])
    assert settings.sources == ['local']
    assert list(build_service(tmp_path, settings).backends) == ['local']
    code = '''import sys
from ajagent.rag.service import RagSettings, build_service
build_service('.', RagSettings())
assert 'torch' not in sys.modules
assert 'transformers' not in sys.modules
'''
    subprocess.run([sys.executable, '-c', code], check=True)


def test_rerank_failure_is_explicit():
    class FailedRanker:
        def score(self, *args):
            raise ImportError()
    service = RetrievalService({'local': Backend([Hit('1', 'local', 'a', 'text')])}, FailedRanker())
    result = service.search('q')
    assert len(result.hits) == 1 and 'fusion' in result.warnings[0]


@pytest.mark.parametrize('expected', [False, True])
def test_agent_cli_registration(tmp_path, monkeypatch, expected):
    import argparse
    import ajagent.cli as cli
    from ajagent.core.types import RunResult
    config = tmp_path / 'config.toml'
    config.write_text('[tools]\nenabled=' + str(['files', 'database'] + (['retrieval'] if expected else [])).replace("'", '"') + '\n[rag]\nrerank=false\nsources=["local"]\n')
    from ajagent.options import Options
    settings = argparse.Namespace(options=Options(), memory=True, context_tokens=32000, output_tokens=4096, workspace=tmp_path, config_path=config, provider='deepseek', model='test',
                                  max_steps=2, max_output_chars=6000)
    monkeypatch.setattr(cli, 'load_config', lambda _, **kwargs: settings)
    monkeypatch.setattr(cli, 'DeepSeekModel', lambda *args: object())
    def run(prompt, model, registry, *args, **kwargs):
        assert ('retrieve' in registry) == expected
        assert {'list_files', 'read_file', 'search_files', 'query_sqlite'} <= registry.keys()
        return RunResult('completed', 'hello', 1, {})
    monkeypatch.setattr(cli, 'run_agent', run)
    # Use the ordinary registry with minimal settings for its constructors.
    settings.tool_timeout = 5
    monkeypatch.setattr(sys, 'argv', ['ajagent', '--config', str(config), 'hello'])
    assert cli.main() == 0


def test_failed_web_tool_is_error_but_partial_results_survive():
    from ajagent.tools.common.registry import execute
    from ajagent.core.types import ToolCall
    class FailedWeb:
        def search(self, *args, **kwargs):
            return SearchResult(warnings=['Tavily unavailable'], diagnostics={
                'web': {'status': 'error', 'attempts': 1}})
    service = RetrievalService({'web': FailedWeb()})
    tool = retrieval_tool(service)
    call = ToolCall('1', 'retrieve', '{"query":"q"}')
    result = execute(call, {'retrieve': tool})
    assert not result.ok and result.error['code'] == 'retrieval_unavailable'
    service.backends['local'] = Backend([Hit('1', 'local', 'a.txt', 'evidence')])
    tool = retrieval_tool(service)
    result = execute(call, {'retrieve': tool})
    assert result.ok and result.data['hits'] and result.data['warnings']
    assert result.data['diagnostics']['web']['attempts'] == 1
