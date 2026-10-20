"""Tavily request contract, error propagation and absence of automatic retries."""
import json as json_module
from contextlib import contextmanager
from unittest.mock import patch
import requests
from tavily import TavilyClient
import pytest
from ajagent.rag.web.search import WebSearch, TavilySettings, canonical_url
from ajagent.rag.service import RagSettings


def response(status, *, json=None, text=""):
    result = requests.Response()
    result.status_code = status
    result.url = "https://api.tavily.com/search"
    result._content = (json_module.dumps(json) if json is not None else text).encode()
    return result


@contextmanager
def sdk_client(handler):
    client = TavilyClient(api_key="test-key")
    assert client.session.get_adapter("https://").max_retries.total == 0
    def send(request, **kwargs):
        return handler(request)
    with patch.object(client.session, "send", side_effect=send):
        yield client
    client.session.close()


def test_search_contract_dedup_and_scores():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url == 'https://api.tavily.com/search'
        assert request.headers['Authorization'] == 'Bearer test-key'
        data = json_module.loads(request.body)
        assert data['max_results'] == 20 and data['search_depth'] == 'basic'
        assert data['include_answer'] is False and data['auto_parameters'] is False
        return response(200, json={'request_id': 'test-request', 'results': [
            {'url': 'https://example.org/a?utm_source=x', 'content': 'Evidence', 'title': 'Page', 'score': .8},
            {'url': 'https://example.org/a', 'content': 'Duplicate'},
            {'url': 'javascript:bad', 'content': 'Bad'}]})
    with sdk_client(handler) as client:
        search = WebSearch(TavilySettings(api_key='test-key'), client)
        result = search.search('q', 30)
        assert len(result.hits) == 1
        assert result.hits[0].score == .8
        assert result.hits[0].metadata['engine'] == 'tavily'
        assert result.hits[0].metadata['evidence_type'] == 'search_excerpt'
        assert result.diagnostics['web']['request_id'] == 'test-request'
        search.search('q', 30)  # No cache or cooldown; each call makes one request.
    assert len(requests) == 2
    assert canonical_url('https://Example.org/a?x=1#part') == 'https://example.org/a?x=1'


@pytest.mark.parametrize('status', [400, 401, 422, 429, 432, 433, 500])
def test_http_errors_are_returned_without_retry_or_key_leak(status):
    calls = []
    def handler(request):
        calls.append(request)
        return response(status, json={'detail': {'error': 'Failed test-key tvly-another-secret'}})
    with sdk_client(handler) as client:
        result = WebSearch(TavilySettings(api_key='test-key'), client).search('q')
    assert len(calls) == 1 and not result.hits
    if status in {422, 500}:
        assert result.diagnostics['web']['http_status'] == status
    assert result.diagnostics['web']['attempts'] == 1
    assert 'test-key' not in str(result) and 'tvly-another' not in str(result)


@pytest.mark.parametrize('error,code', [(requests.exceptions.ReadTimeout('timeout'), 'timeout'),
                                       (requests.exceptions.ConnectionError('network failed'), 'network_error')])
def test_transport_errors_do_not_retry(error, code):
    calls = []
    def handler(request):
        calls.append(request)
        raise error
    with sdk_client(handler) as client:
        result = WebSearch(TavilySettings(api_key='test-key'), client).search('q')
    assert len(calls) == 1 and result.diagnostics['web']['error'] == code


def test_empty_invalid_and_non_json_error_responses():
    cases = [(response(200, json={'results': []}), 'empty'),
             (response(200, json={'results': {}}), 'error'),
             (response(200, text='not JSON'), 'error'),
             (response(503, text='private server dump'), 'error')]
    for reply, status in cases:
        with sdk_client(lambda request: reply) as client:
            result = WebSearch(TavilySettings(api_key='test-key'), client).search('q')
        assert result.diagnostics['web']['status'] == status
        assert 'private server dump' not in str(result)


def test_missing_key_does_not_call_network():
    def handler(request):
        pytest.fail('No key must not make a request')
    with sdk_client(handler) as client:
        result = WebSearch(client=client).search('q')
    assert result.diagnostics['web']['error'] == 'missing_api_key'
    assert result.diagnostics['web']['attempts'] == 0


def test_toml_credentials_and_environment_override(tmp_path, monkeypatch):
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    config = tmp_path / 'config.toml'
    config.write_text('[tavily]\napi_key="test-secret"\ntimeout=12\nsearch_depth="advanced"\n')
    settings = RagSettings.load(config)
    assert settings.tavily.api_key == 'test-secret' and settings.tavily.timeout == 12
    assert 'test-secret' not in repr(settings) and 'test-secret' not in repr(settings.tavily)
    monkeypatch.setenv('TAVILY_API_KEY', 'env-secret')
    assert RagSettings.load(config).tavily.api_key == 'env-secret'
    config.write_text('[tavily]\ntimeout=-1\n')
    with pytest.raises(ValueError):
        RagSettings.load(config)


def test_content_limit_and_advanced_parameters():
    from unittest.mock import Mock
    client = Mock()
    client.search.return_value = {'results': [
        {'url': 'https://example.org', 'content': '证据' * 1500, 'score': .9}]}
    settings = TavilySettings(api_key='test-key', search_depth='advanced', max_content_chars=500)
    result = WebSearch(settings, client).search('question')
    assert len(result.hits[0].text) == 500
    assert result.hits[0].metadata['truncated'] is True
    assert client.search.call_args.kwargs['search_depth'] == 'advanced'
    assert client.search.call_args.kwargs['timeout'] == 30


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, 20001])
def test_invalid_content_limit(limit):
    with pytest.raises(ValueError):
        TavilySettings(max_content_chars=limit).validate()
