"""Cached reads retain exact evidence and invalidate after external changes."""
from dataclasses import replace
import os
import pytest
from ajagent.options import DEFAULTS
from ajagent.tools.files.workspace import Workspace
from ajagent.tools.common.base import ToolError


def workspace(root, capacity=8388608):
    return Workspace(root, root/'config.local.toml', options=replace(DEFAULTS.tools, read_cache_bytes=capacity))


def test_read_equivalence_hits_and_external_same_size_edit(tmp_path):
    path = tmp_path/'a.txt'
    path.write_text('first\nsecond\n')
    cached, plain = workspace(tmp_path), workspace(tmp_path, 0)
    assert cached.read_file('a.txt') == plain.read_file('a.txt')
    assert cached.read_file('a.txt', 2, 1) == plain.read_file('a.txt', 2, 1)
    assert cached.read_cache.hits == 1
    before = path.stat()
    path.write_text('other\nsecond\n')
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert cached.read_file('a.txt') == plain.read_file('a.txt')
    assert cached.read_cache.misses == 2
    temp = tmp_path/'replacement'
    temp.write_text('third\nsecond\n')
    temp.replace(path)
    assert cached.read_file('a.txt') == plain.read_file('a.txt')
    path.unlink()
    with pytest.raises(ToolError):
        cached.read_file('a.txt')


def test_cache_does_not_bypass_boundary_or_mutate_results(tmp_path):
    (tmp_path/'a.txt').write_text('hello')
    ws = workspace(tmp_path)
    first, _ = ws.read_file('a.txt')
    first['lines'][0]['text'] = 'corrupted'
    assert ws.read_file('a.txt')[0]['lines'][0]['text'] == 'hello'
    (tmp_path/'a.txt').unlink()
    (tmp_path/'a.txt').symlink_to(tmp_path/'config.local.toml')
    with pytest.raises(ToolError, match='Symlinks'):
        ws.read_file('a.txt')


def test_cache_is_bounded_and_can_be_disabled(tmp_path):
    for i in range(4):
        (tmp_path/f'{i}.txt').write_text('x'*100)
    ws = workspace(tmp_path, 400)
    for i in range(4):
        ws.read_file(f'{i}.txt')
    assert ws.read_cache.bytes <= 400 and len(ws.read_cache.entries) < 4
    plain = workspace(tmp_path, 0)
    plain.read_file('0.txt'); plain.read_file('0.txt')
    assert plain.read_cache.hits == 0 and not plain.read_cache.entries
