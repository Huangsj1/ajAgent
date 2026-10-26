"""Disk-backed recall must not resurrect archived groups in resident memory."""
import json
import sqlite3
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import pytest
from ajagent.context.builder import ContextBuilder
from ajagent.memory.session import Session
from ajagent.options import DEFAULTS
from ajagent.tools.history.recall import History


def archive(session, count):
    builder = ContextBuilder()
    builder.bind(session)
    builder.handoff('Durable state: continue implementation', session.groups)
    assert session.groups.start == count and session.groups == []
    return builder


def test_eviction_resume_and_monotonic_ids(tmp_path):
    session = Session(tmp_path, 'deepseek', 'test')
    for i in range(30):
        session.record([{'role': 'user', 'content': f'archived-{i} ' + 'data ' * 1000}])
    archive(session, 30)
    session.record([{'role': 'user', 'content': 'active tail'}])
    assert session.group_count == 31 and len(session.groups) == 1
    restored = Session(tmp_path, 'deepseek', 'test', session.id)
    assert restored.group_count == 31 and restored.groups.start == 30
    assert restored.groups == [[{'role': 'user', 'content': 'active tail'}]]
    assert History(restored).search('archived-0')[0]['hits'][0]['group_id'] == 'g1'
    assert 'archived-29' in History(restored).read('g30')[0]['text']
    restored.record([{'role': 'assistant', 'content': 'next'}])
    assert restored.group_count == 32
    assert json.loads(restored.path.read_text().splitlines()[-1])['group_id'] == 'g32'
    assert len(restored.groups) == 2


@pytest.mark.parametrize('damage', ['delete', 'corrupt'])
def test_index_rebuild_and_incremental_append(tmp_path, damage):
    session = Session(tmp_path, 'deepseek', 'test')
    session.record([{'role': 'user', 'content': 'old needle'}])
    archive(session, 1)
    index = session.history.path
    if damage == 'delete':
        index.unlink()
    else:
        index.write_bytes(b'not a database')
    restored = Session(tmp_path, 'deepseek', 'test', session.id)
    assert restored.groups == [] and restored.groups.start == 1
    assert History(restored).search('needle')[0]['hits']
    restored.record([{'role': 'user', 'content': 'new needle'}])
    hits = History(restored).search('needle')[0]['hits']
    assert [hit['group_id'] for hit in hits] == ['g1', 'g2']
    with sqlite3.connect(index) as db:
        assert db.execute('select count(*) from groups').fetchone()[0] == 2


def test_search_uses_index_without_reserializing_history(tmp_path, monkeypatch):
    session = Session(tmp_path, 'deepseek', 'test')
    for i in range(20):
        session.record([{'role': 'user', 'content': f'payload {i}: rare-literal' if i == 17 else 'other'}])
    archive(session, 20)
    def fail(*args, **kwargs):
        pytest.fail('Search must not parse or serialize the session log again')
    monkeypatch.setattr(json, 'dumps', fail)
    # Only the boolean FTS capability flag is decoded, never message JSON.
    original = json.loads
    def decode(value, *args, **kwargs):
        assert value in ('true', 'false')
        return original(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', decode)
    data, _ = History(session).search('rare-literal')
    assert data['engine'] == 'trigram' and data['hits'][0]['group_id'] == 'g18'
    assert session.groups == []


def test_short_queries_are_bounded_and_unicode_offsets_are_original(tmp_path):
    session = Session(tmp_path, 'deepseek', 'test')
    for text in ['none', 'none', '目标 Straße ΩMEGA "quoted" a%b']:
        session.record([{'role': 'user', 'content': text}])
    history = History(session, replace(DEFAULTS.context, history_scan_groups=2, history_page_chars=8))
    first, _ = history.search('目标')
    assert first['engine'] == 'bounded_scan' and first['hits'] == [] and first['next_cursor'] == 2
    second, _ = history.search('目标', first['next_cursor'])
    assert second['hits'][0]['group_id'] == 'g3'
    for query in ['STRASSE', 'ωmega', 'a%b']:
        data, _ = history.search(query)
        hit = data['hits'][0]
        page, _ = history.read(hit['group_id'], hit['offset'])
        assert page['text'].casefold().startswith(query.casefold())


def test_parallel_reads_do_not_share_sqlite_connections(tmp_path):
    session = Session(tmp_path, 'deepseek', 'test')
    session.record([{'role': 'user', 'content': 'find needle'}])
    archive(session, 1)
    history = History(session)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: history.search('needle')[0]['hits'], range(20)))
    assert all(hits[0]['group_id'] == 'g1' for hits in results)


def test_restore_detects_same_size_log_edit_and_falls_back(tmp_path):
    session = Session(tmp_path, 'deepseek', 'test')
    session.record([{'role': 'user', 'content': 'old content'}])
    archive(session, 1)
    text = session.path.read_text().replace('old content', 'new content')
    session.path.write_text(text)
    restored = Session(tmp_path, 'deepseek', 'test', session.id)
    assert restored.groups.start == 0
    builder = ContextBuilder()
    builder.bind(restored)
    builder.restore(restored.checkpoint, restored.groups)
    assert builder.covered == 0
    assert History(restored).search('new content')[0]['hits']


def test_legacy_checkpoint_migrates_without_loading_archived_groups(tmp_path):
    from ajagent.context.compaction import fingerprint
    session = Session(tmp_path, 'deepseek', 'test')
    first = [{'role': 'user', 'content': 'old evidence'}]
    session.record(first)
    session.record([{'role': 'user', 'content': 'tail'}])
    session.append({'event': 'context', 'format': 2, 'summary': 'legacy state', 'summarized_groups': 1,
                    'prefix_hash': fingerprint([first]), 'episodes': [
                        {'start': 0, 'end': 1, 'hash': fingerprint([first]), 'state': {}, 'kind': 'task'}]})
    session.flush()
    restored = Session(tmp_path, 'deepseek', 'test', session.id)
    assert restored.groups.start == 1 and len(restored.groups) == 1
    builder = ContextBuilder()
    builder.bind(restored)
    assert builder.covered == 1 and builder.summary == 'legacy state'
    builder.handoff('new state', restored.groups)
    assert restored.checkpoint['format'] == 3
    assert History(restored).read('g1')[0]['text'] == json.dumps(first, ensure_ascii=False, sort_keys=True)


def test_live_deleted_index_is_rebuilt_and_empty_history_is_searchable(tmp_path):
    session = Session(tmp_path, 'deepseek', 'test')
    history = History(session)
    assert history.search('nothing')[0]['hits'] == []
    session.record([{'role': 'user', 'content': 'needle'}])
    session.history.path.unlink()
    assert history.search('needle')[0]['hits'][0]['group_id'] == 'g1'


def test_sqlite_without_trigram_falls_back_to_bounded_scan(tmp_path, monkeypatch):
    class NoTrigram(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith('create virtual table'):
                raise sqlite3.OperationalError('no such tokenizer: trigram')
            return super().execute(sql, *args, **kwargs)
    session = Session(tmp_path, 'deepseek', 'test')
    monkeypatch.setattr(session.history, 'connect', lambda: sqlite3.connect(session.history.path, factory=NoTrigram))
    for text in ['other', 'other', 'needle']:
        session.record([{'role': 'user', 'content': text}])
    history = History(session, replace(DEFAULTS.context, history_scan_groups=2))
    first, _ = history.search('needle')
    assert first['engine'] == 'bounded_scan' and first['hits'] == []
    assert history.search('needle', first['next_cursor'])[0]['hits'][0]['group_id'] == 'g3'
