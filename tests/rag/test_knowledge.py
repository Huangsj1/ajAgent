"""External sources, reusable index manifests, and independent search roots."""
import json
from pathlib import Path
import numpy as np
import pytest
from ajagent.rag.knowledge import update_knowledge, open_knowledge
from ajagent.rag.local.search import LocalSearch
from ajagent.rag.service import RagSettings, build_service


class Encoder:
    dimensions = 3
    fingerprint = 'external-test-v1'

    def encode(self, texts, query=False):
        rows = np.array([[t.count('apple'), t.count('banana'), .01] for t in texts], dtype=np.float32)
        return rows / np.linalg.norm(rows, axis=1, keepdims=True)


def test_external_sources_and_update_from_manifest(tmp_path):
    source = tmp_path / 'outside' / 'notes'
    source.mkdir(parents=True)
    output = tmp_path / 'elsewhere' / 'db'
    (source / 'a.txt').write_text('apple')
    encoder = Encoder()
    assert update_knowledge(source, output, encoder=encoder)['added'] == 1
    index = open_knowledge(output, encoder=encoder)
    assert index.root == source and index.search('apple').hits[0].text == 'apple'
    (source / 'a.txt').write_text('banana')
    (source / 'b.txt').write_text('apple fresh')
    assert index.update()['updated'] == 1
    (source / 'a.txt').unlink()
    assert open_knowledge(output, encoder=encoder).update()['deleted'] == 1
    assert open_knowledge(output, encoder=encoder).search('apple').hits[0].uri == 'b.txt'


def test_output_inside_source_not_reindexed(tmp_path):
    (tmp_path / 'a.txt').write_text('apple')
    output = tmp_path / 'generated'
    encoder = Encoder()
    assert update_knowledge(tmp_path, output, encoder=encoder)['added'] == 1
    assert update_knowledge(tmp_path, output, encoder=encoder)['unchanged'] == 1
    assert len(open_knowledge(output, encoder=encoder).search('apple').hits) == 1
    with pytest.raises(ValueError):
        update_knowledge(tmp_path, tmp_path, encoder=encoder)


def test_external_single_file_does_not_search_siblings(tmp_path):
    selected = tmp_path / 'chosen.txt'
    selected.write_text('apple selected')
    (tmp_path / 'other.txt').write_text('apple other')
    result = LocalSearch(selected).search('apple')
    assert len(result.hits) == 1 and result.hits[0].uri == 'chosen.txt'
    assert not LocalSearch(selected).search('apple', glob='*.py').hits


def test_index_only_service_and_unambiguous_locations(tmp_path, monkeypatch):
    import ajagent.rag.semantic.encoder as model
    monkeypatch.setattr(model, 'QwenEncoder', lambda *a, **k: Encoder())
    project = tmp_path / 'project'
    project.mkdir()
    source = tmp_path / 'external'
    source.mkdir()
    (source / 'a.txt').write_text('apple')
    output = tmp_path / 'output'
    update_knowledge(source, output, encoder=Encoder())
    config = project / 'config.toml'
    config.write_text('[rag]\nindex="../output"\nlocal_path="../external"\nrerank=false\n')
    settings = RagSettings.load(config)
    service = build_service(project, settings, config)
    result = service.search('apple')
    assert len(result.hits) == 1  # Local and semantic share an absolute source identity.
    assert result.hits[0].uri == str(source / 'a.txt')
    assert set(result.hits[0].metadata['backend_scores']) == {'local', 'semantic'}
    assert service.backends['semantic'].root == source


def test_cli_build_update_and_index_only_search(tmp_path, monkeypatch, capsys):
    import sys
    import ajagent.rag.cli as cli
    import ajagent.rag.knowledge as knowledge
    import ajagent.rag.semantic.encoder as model
    monkeypatch.setattr(knowledge, 'QwenEncoder', lambda *a, **k: Encoder())
    monkeypatch.setattr(model, 'QwenEncoder', lambda *a, **k: Encoder())
    source = tmp_path / 'notes'
    source.mkdir()
    (source / 'a.txt').write_text('apple')
    output = tmp_path / 'output'
    config = tmp_path / 'test.toml'
    config.write_text('[rag]\nrerank=false\n')
    def run(*args):
        monkeypatch.setattr(sys, 'argv', ['ajagent-rag', '--config', str(config), *args])
        assert cli.main() == 0
        return json.loads(capsys.readouterr().out)
    assert run('index', '--source', str(source), '--output', str(output))['added'] == 1
    assert run('update', '--index', str(output))['unchanged'] == 1
    result = run('search', 'apple', '--index', str(output), '--sources', 'semantic')
    assert result['hits'][0]['uri'] == str(source / 'a.txt')


def test_agent_cli_forwards_external_paths(tmp_path, monkeypatch):
    import argparse
    import sys
    import ajagent.cli as cli
    import ajagent.rag.service as service
    from ajagent.core.types import RunResult
    config = tmp_path / 'config.toml'
    config.write_text('[rag]\nrerank=false\nsources=["local"]\n')
    settings = argparse.Namespace(workspace=tmp_path, config_path=config, provider='deepseek', model='test',
                                  max_steps=2, max_output_chars=6000, tool_timeout=5)
    monkeypatch.setattr(cli, 'load_config', lambda _: settings)
    monkeypatch.setattr(cli, 'DeepSeekModel', lambda *a: object())
    original = service.build_service
    def build(root, rag, config_path):
        assert rag.index == str(tmp_path / 'outside-index')
        assert rag.local_path == str(tmp_path / 'outside-files')
        return original(root, rag, config_path)
    monkeypatch.setattr(service, 'build_service', build)
    monkeypatch.setattr(cli, 'run_agent', lambda *a, **k: RunResult('completed', 'hello', 1, {}))
    monkeypatch.setattr(sys, 'argv', ['ajagent', '--config', str(config), '--rag',
        '--rag-index', str(tmp_path / 'outside-index'), '--rag-local-path', str(tmp_path / 'outside-files'), 'hello'])
    assert cli.main() == 0


def test_moved_index_and_source_mismatch(tmp_path):
    import shutil
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'a.txt').write_text('apple')
    encoder = Encoder()
    output = tmp_path / 'index'
    update_knowledge(source, output, encoder=encoder)
    moved = tmp_path / 'moved-index'
    shutil.move(output, moved)
    assert open_knowledge(moved, encoder=encoder).search('apple').hits[0].text == 'apple'
    other = tmp_path / 'other'
    other.mkdir()
    with pytest.raises(ValueError, match='differs'):
        update_knowledge(other, moved, encoder=encoder)
    assert open_knowledge(moved, encoder=encoder).search('apple').hits
