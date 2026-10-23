"""Real persistent stdio and Streamable HTTP sessions, using local fixtures."""
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
import httpx
import pytest
from ajagent.tools.mcp.config import ServerConfig, load_servers
from ajagent.tools.mcp.manager import MCPManager
from ajagent.tools.common.base import ToolError

SERVER = Path(__file__).resolve().parents[1] / 'mcp/state_server.py'


def test_stdio_session_persists_and_multi_server_namespaces(tmp_path):
    pytest.importorskip('mcp')
    script = SERVER
    manager = MCPManager([ServerConfig(name, command=sys.executable, args=[str(script)], read_tools=['count']) for name in ('one', 'two')])
    manager.connect()
    clients = list(manager.clients.values())
    try:
        assert {tool.name for tool in manager.specs()} == {'mcp_one_count', 'mcp_two_count'}
        a = manager.call('one', 'count')[0]['structured_content']
        b = manager.call('one', 'count')[0]['structured_content']
        other = manager.call('two', 'count')[0]['structured_content']
        assert a['pid'] == b['pid'] != other['pid']
        assert (a['calls'], b['calls'], other['calls']) == (1, 2, 1)
        manager.reconnect('one')
        assert manager.call('one', 'count')[0]['structured_content']['calls'] == 1
    finally:
        manager.close()
    assert all(not client.thread.is_alive() for client in clients)


def test_remote_streamable_http_persistent_session(tmp_path):
    pytest.importorskip('mcp')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, str(SERVER), '--transport', 'streamable-http', '--port', str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    manager = MCPManager([ServerConfig('remote', transport='http', url=f'http://127.0.0.1:{port}/mcp')])
    try:
        deadline = time.monotonic() + 10
        with httpx.Client() as client:
            while True:
                try:
                    client.get(f'http://127.0.0.1:{port}/mcp', timeout=.2)
                    break
                except httpx.HTTPError:
                    if time.monotonic() > deadline:
                        pytest.fail('Fixture HTTP server did not start')
                    time.sleep(.05)
        manager.connect()
        first = manager.call('remote', 'count')[0]['structured_content']
        second = manager.call('remote', 'count')[0]['structured_content']
        assert first['pid'] == second['pid'] and second['calls'] == 2
        assert manager.specs()[0].effect == 'execute'
    finally:
        manager.close()
        process.terminate()
        process.wait(timeout=5)


def test_mcp_config_environment_and_validation(tmp_path, monkeypatch):
    path = tmp_path / 'mcp.local.toml'
    monkeypatch.setenv('MCP_TEST_TOKEN', 'private-test-value')
    path.write_text('[servers.remote]\ntransport="http"\nurl="https://example.test/mcp"\n[servers.remote.headers]\nAuthorization="Bearer ${MCP_TEST_TOKEN}"\n')
    config = load_servers(path)[0]
    assert config.headers['Authorization'] == 'Bearer private-test-value'
    assert 'private-test-value' not in repr(config)
    path.write_text('[servers.bad]\ntransport="unknown"\n')
    with pytest.raises(ValueError):
        load_servers(path)
