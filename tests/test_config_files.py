"""Configuration composition and repository layout regression tests."""
from pathlib import Path
import pytest
from ajagent.options import read_config, Options, DEFAULT_CONFIG_PATH
from ajagent.tools.runtime.config import ToolConfig
from ajagent.tools.mcp.config import load_servers


def test_includes_merge_tables_and_entry_overrides(tmp_path):
    (tmp_path / 'nested').mkdir()
    (tmp_path / 'nested/model.toml').write_text('[runtime]\nprovider="deepseek"\nmax_steps=2\n')
    (tmp_path / 'runtime.toml').write_text('include=["nested/model.toml"]\n[runtime]\nmax_steps=3\n')
    entry = tmp_path / 'entry.toml'
    entry.write_text('include=["runtime.toml"]\n[runtime]\nmax_steps=4\n')
    assert read_config(entry) == {'runtime': {'provider':'deepseek', 'max_steps':4}}


def test_invalid_includes_fail(tmp_path):
    entry = tmp_path / 'entry.toml'
    entry.write_text('include=["missing.toml"]')
    with pytest.raises(FileNotFoundError):
        read_config(entry)
    entry.write_text('include=["entry.toml"]')
    with pytest.raises(ValueError, match='Circular'):
        read_config(entry)
    entry.write_text('include="bad.toml"')
    with pytest.raises(ValueError, match='list'):
        read_config(entry)


def test_templates_bootstrap_complete_configuration(tmp_path):
    root = Path(__file__).resolve().parents[1]
    folder = tmp_path / 'config'
    folder.mkdir()
    for source in (root / 'config').glob('*.example.toml'):
        (folder / source.name.replace('.example.', '.local.')).write_text(source.read_text())
    entry = tmp_path / DEFAULT_CONFIG_PATH
    config = read_config(entry)
    Options.from_config(config)
    tools = ToolConfig.load(config)
    assert tools.path(tools.skills_path, entry) == tmp_path / 'skills'
    servers = load_servers(tools.path(tools.mcp_config, entry))
    assert servers[0].cwd == str(tmp_path)
    assert servers[0].args == ['mcp/add_server.py']
    assert config['rag']['knowledge'] == '../knowledge'
    assert set(config) >= {'runtime','deepseek','openai','tools','rag','tavily','harness','limits'}
    assert config['deepseek']['api_key'] == config['openai']['api_key'] == config['tavily']['api_key'] == ''
