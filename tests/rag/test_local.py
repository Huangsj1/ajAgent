from pathlib import Path
import pytest
from ajagent.rag.local.search import LocalSearch
from ajagent.rag.local.syntax import definitions


@pytest.fixture
def root(tmp_path):
    (tmp_path / 'sample.py').write_text('@decorator\ndef greet(name):\n    return "hello " + name\n\nclass Box:\n    def open(self):\n        return True\n')
    (tmp_path / 'sample.ts').write_text('export function greet(name: string) { return name; }\nconst plus = (x: number) => x + 1;\n')
    (tmp_path / '.env').write_text('PRIVATE_MATCH')
    (tmp_path / 'config.local.toml').write_text('PRIVATE_MATCH')
    (tmp_path / 'link.txt').symlink_to(tmp_path / '.env')
    return tmp_path


def test_real_rg_literal_regex_and_secret_exclusions(root):
    search = LocalSearch(root)
    assert len(search.search('greet').hits) == 2
    assert search.search(r'def\s+greet', mode='regex').hits[0].start_line == 2
    assert search.search('PRIVATE_MATCH').hits == []
    assert search.search('absent').hits == []
    assert search.search('greet', glob='*.py').hits[0].uri == 'sample.py'
    with pytest.raises(ValueError):
        search.search('[', mode='regex')


def test_actual_tree_sitter_python_typescript(root):
    search = LocalSearch(root)
    hits = search.search('greet', mode='symbol').hits
    assert len(hits) == 2
    assert hits[0].text.startswith('@decorator')
    assert search.search('open', mode='symbol').hits[0].start_line == 6
    assert search.search('plus', mode='symbol').hits[0].title == 'plus'


def test_bad_syntax_and_result_cap(root):
    (root / 'broken.py').write_text('def broken(:')
    result = LocalSearch(root).search('greet', mode='symbol')
    assert any('Cannot parse' in warning for warning in result.warnings)
    assert len(LocalSearch(root).search('greet', limit=1).hits) == 1
