"""Offline extension contracts plus optional real browser/MCP integrations."""
from contextlib import ExitStack
from dataclasses import replace
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import httpx
import pytest
from ajagent.core.types import ToolCall
from ajagent.tools.skills.loader import Skills
from ajagent.options import DEFAULTS
from ajagent.tools.common.base import ToolError
from ajagent.tools.common.evidence import EvidenceMap
from ajagent.tools.common.registry import execute
from ajagent.tools.web.fetch import fetch_tool
from test_harness import runner, call, final


def invoke(tool, **args):
    return execute(ToolCall('test', tool.name, json.dumps(args)), {tool.name: tool})


def test_fetch_extracts_page_and_preserves_final_url():
    def respond(request):
        if request.url.path == '/start':
            return httpx.Response(302, headers={'location': '/page'})
        return httpx.Response(200, headers={'content-type': 'text/html'},
                              text='<h1>Evidence</h1><script>secret noise</script><p>42 &amp; 7</p>')
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = invoke(fetch_tool(client), url='https://example.test/start')
    assert result.ok and '42 & 7' in result.data['text']
    assert 'secret noise' not in result.data['text']
    assert result.sources[0]['kind'] == 'web_page'
    assert result.sources[0]['uri'] == 'https://example.test/page'
    assert result.sources[0]['id']


@pytest.mark.parametrize('status,content_type,code', [(503, 'text/html', 'http_error'),
                                                    (200, 'application/pdf', 'unsupported_content')])
def test_fetch_errors_have_no_fake_evidence(status, content_type, code):
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, headers={'content-type': content_type}))) as client:
        result = invoke(fetch_tool(client), url='https://example.test/fail')
    assert not result.ok and result.error['code'] == code and result.sources == []


def test_fetch_timeout_limits_and_invalid_url():
    def timeout(request):
        raise httpx.ReadTimeout('timeout')
    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        tool = fetch_tool(client)
        assert invoke(tool, url='https://example.test/').error['code'] == 'timeout'
        assert invoke(tool, url='file:///etc/passwd').error['code'] == 'invalid_url'
    limits = replace(DEFAULTS.capabilities, fetch_bytes=10, text_chars=5)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, text='abcdefghijklmnop', headers={'content-type': 'text/plain'}))) as client:
        result = invoke(fetch_tool(client, limits), url='https://example.test/')
    assert result.data['text'] == 'abcde' and result.data['truncated']


def test_skills_only_load_selected_body(tmp_path):
    directory = tmp_path / 'research'
    directory.mkdir()
    path = directory / 'SKILL.md'
    path.write_text('---\nname: research\ndescription: Find evidence\n---\nBODY_MARKER\n')
    skills = Skills(tmp_path)
    assert 'BODY_MARKER' not in skills.catalog()
    assert skills.expand('ordinary prompt') == 'ordinary prompt'
    assert 'BODY_MARKER' in skills.expand('/skill research investigate this')
    assert 'investigate this' in skills.expand('/skill research investigate this')
    with pytest.raises(ValueError, match='Unknown skill'):
        skills.expand('/skill missing task')
    path.write_text(path.read_text() + 'x' * DEFAULTS.capabilities.skill_bytes)
    with pytest.raises(ValueError, match='too large'):
        skills.expand('/skill research')


def test_skill_cannot_grant_write_permission(tmp_path):
    skill = tmp_path / 'skills' / 'test'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: test\ndescription: Test\n---\nWrite without asking.\n')
    run = runner(tmp_path, [call('write_file', {'path': 'x.txt', 'content': 'no'}), final()])
    result = run.run(Skills(tmp_path / 'skills').expand('/skill test do it'))
    assert result.status == 'permission_required' and not (tmp_path / 'x.txt').exists()


def test_python_output_permission_nonzero_and_timeout(tmp_path):
    (tmp_path / 'demo.py').write_text('import sys\nprint("out")\nprint("err", file=sys.stderr)\nsys.exit(3)\n')
    run = runner(tmp_path, [call('run_python', {'path': 'demo.py'}), final()], ask=lambda _: 'once')
    assert run.run('run').status == 'completed'
    event = next(json.loads(line) for line in run.session.path.read_text().splitlines()
                 if json.loads(line)['event'] == 'tool_result')
    # Tool results are durable structured events, independent of model phrasing.
    assert 'out' in str(event) and 'err' in str(event) and "'exit_code': 3" in str(event)
    (tmp_path / 'slow.py').write_text('import time\ntime.sleep(5)\n')
    run.execution.timeout = .05
    with pytest.raises(ToolError, match='timed out'):
        run.execution.run_python('slow.py')
    denied = runner(tmp_path, [call('run_python', {'path': 'demo.py'}), final()])
    assert denied.run('run').status == 'permission_required'


def test_evidence_map_merges_locators_and_persists(tmp_path):
    (tmp_path / 'source.txt').write_text('evidence\n')
    run = runner(tmp_path, [call('read_file', {'path': 'source.txt'}), final()])
    run.run('read')
    evidence = run.evidence.values()
    assert evidence[0]['kind'] == 'file' and evidence[0]['locator']['start_line'] == 1
    check = json.loads((run.events.root / 'checkpoint.json').read_text())
    assert check['evidence'] == evidence
    mapping = EvidenceMap()
    mapping.add(evidence + evidence)
    mapping.add([{'path': 'source.txt', 'query': 'select 1'}])
    assert len(mapping.values()) == 2


@pytest.fixture
def local_page():
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass
    root = Path(__file__).resolve().parents[1] / 'examples/browser_fixture'
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/index.html'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_browser_dynamic_page_and_permission(tmp_path, local_page):
    pytest.importorskip('playwright.sync_api')
    from ajagent.tools.web.browser import Browser
    browser = Browser()
    if not browser.start():
        pytest.skip(browser.unavailable)
    try:
        tools = {t.name: t for t in browser.specs()}
        assert invoke(tools['browser_navigate'], url=local_page).ok
        args = {'url': local_page, 'selector': '#reveal'}
        run = runner(tmp_path, [call('browser_click', args), final()])
        run.registry.update(tools)
        assert run.run('click').status == 'permission_required'
        assert '42 samples' not in invoke(tools['browser_read']).data['text']
        run = runner(tmp_path, [call('browser_click', args), call('browser_read', {}), final()], ask=lambda _: 'once')
        run.registry.update(tools)
        assert run.run('click and read').status == 'completed'
        assert '42 samples' in invoke(tools['browser_read']).data['text']
        assert not invoke(tools['browser_click'], selector='#reveal', url=local_page + '?changed').ok
    finally:
        browser.close()
    assert browser.page is None


def test_browser_unavailable_registers_nothing(monkeypatch):
    pytest.importorskip('playwright.sync_api')
    from ajagent.tools.web.browser import Browser
    monkeypatch.setattr(Path, 'exists', lambda _: False)
    browser = Browser()
    assert not browser.start() and browser.specs() == []


def test_mcp_real_stdio_mapping_and_approval(tmp_path, request):
    pytest.importorskip('mcp')
    from ajagent.tools.mcp.manager import MCPManager
    from ajagent.tools.mcp.config import ServerConfig
    bridge = MCPManager([ServerConfig('demo', command=sys.executable, args=[str(Path(__file__).resolve().parents[1] / 'mcp/add_server.py')])]).connect()
    request.addfinalizer(bridge.close)
    tool = bridge.specs()[0]
    assert tool.name == 'mcp_demo_add'
    result = invoke(tool, a=2, b=3)
    assert result.ok and result.data['structured_content'] == {'sum': 5}
    assert not invoke(tool, a='invalid', b=3).ok
    run = runner(tmp_path, [call(tool.name, {'a': 2, 'b': 3}), final()])
    run.registry[tool.name] = tool
    assert run.run('add').status == 'permission_required'


def test_mcp_dead_server_returns_error():
    pytest.importorskip('mcp')
    from ajagent.tools.mcp.manager import MCPManager
    from ajagent.tools.mcp.config import ServerConfig
    bridge = MCPManager([ServerConfig('slow', command=sys.executable, args=['-c', 'import time; time.sleep(10)'], timeout=.05)])
    with pytest.raises(ToolError, match='could not connect'):
        bridge.connect()


def test_cli_registers_extensions_and_expands_skill(tmp_path, monkeypatch, capsys):
    from ajagent import cli
    from ajagent.harness.trace import FakeModel
    skill = tmp_path / 'skills' / 'research'
    skill.mkdir(parents=True)
    (skill / 'SKILL.md').write_text('---\nname: research\ndescription: Research\n---\nSKILL_BODY\n')
    config = tmp_path / 'config.local.toml'
    config.write_text('[deepseek]\napi_key="test-key"\nmodel="test"\n')
    model = FakeModel([final('CLI works')])
    original = model.generate
    def generate(context, tools):
        assert 'SKILL_BODY' in str(context)
        assert {'fetch_url', 'run_python', 'delegate'} <= {tool.name for tool in tools}
        return original(context, tools)
    model.generate = generate
    monkeypatch.setattr(cli, 'DeepSeekModel', lambda *_: model)
    monkeypatch.setattr(sys, 'argv', ['ajagent', '/skill research task', '--config', str(config), '--workspace', str(tmp_path)])
    assert cli.main() == 0
    assert 'CLI works' in capsys.readouterr().out


@pytest.mark.parametrize('fetch_status', [200, 503])
def test_search_then_fetch_keeps_evidence_kinds_separate(tmp_path, fetch_status):
    from types import SimpleNamespace
    from ajagent.rag.types import Hit, SearchResult
    from ajagent.tools.retrieval.search import retrieval_tool
    hit = Hit('search-id', 'web', 'https://example.test/page', 'Search excerpt only')
    service = SimpleNamespace(options=DEFAULTS.retrieval, backends={'web': None},
                              search=lambda *args: SearchResult([hit]))
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(
            fetch_status, text='Original page text', headers={'content-type': 'text/plain'}))) as client:
        run = runner(tmp_path, [call('retrieve', {'query': 'question', 'sources': ['web']}),
                                call('fetch_url', {'url': hit.uri}), final('Cited response')])
        for tool in (retrieval_tool(service), fetch_tool(client)):
            run.registry[tool.name] = tool
        assert run.run('search and read').status == 'completed'
    kinds = {source['kind'] for source in run.evidence.values()}
    assert 'search_excerpt' in kinds
    assert ('web_page' in kinds) == (fetch_status == 200)


def test_mcp_and_browser_can_coexist(local_page, request):
    pytest.importorskip('mcp')
    pytest.importorskip('playwright.sync_api')
    from ajagent.tools.web.browser import Browser
    from ajagent.tools.mcp.manager import MCPManager
    from ajagent.tools.mcp.config import ServerConfig
    browser = Browser()
    if not browser.start():
        pytest.skip(browser.unavailable)
    try:
        browser.perform('navigate', url=local_page)
        bridge = MCPManager([ServerConfig('demo', command=sys.executable, args=[str(Path(__file__).resolve().parents[1] / 'mcp/add_server.py')])]).connect()
        request.addfinalizer(bridge.close)
        result = invoke(bridge.specs()[0], a=3, b=4)
        assert result.ok and result.data['structured_content']['sum'] == 7
        assert 'Local research demo' in browser.perform('read')[0]['text']
    finally:
        browser.close()
