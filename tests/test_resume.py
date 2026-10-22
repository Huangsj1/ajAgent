"""Recover complete batches; never replay unknown effects automatically."""
import json
from ajagent.memory.session import Session
from test_harness import runner, call, final


def test_completed_effect_is_not_replayed_on_resume(tmp_path):
    first = runner(tmp_path, [call('write_file', {'path': 'demo.txt', 'content': 'one'}), final()], ask=lambda _: 'session')
    assert first.run('write').status == 'completed'
    session = Session(tmp_path / '.ajagent', 'deepseek', 'fake', first.session.id)
    second = runner(tmp_path, [final('Already done')], session=session)
    assert second.run('continue').status == 'completed'
    assert second.budget.tool_calls == 0
    assert (tmp_path / 'demo.txt').read_text() == 'one'
    assert second.session.usage['input_tokens'] == 50
    assert session.grants


def test_unknown_effect_blocks_until_acknowledged(tmp_path):
    first = runner(tmp_path, [call('write_file', {'path': 'demo.txt', 'content': 'one'})], ask=lambda _: 'once')
    def interrupt(call, args, result):
        raise KeyboardInterrupt()
    first.hooks.after_tool.append(interrupt)
    assert first.run('write').status == 'cancelled'
    assert (tmp_path / 'demo.txt').read_text() == 'one'
    checkpoint = json.loads((first.events.root / 'checkpoint.json').read_text())
    assert checkpoint['pending'] and checkpoint['pending'][0]['status'] == 'unknown'
    session = Session(tmp_path / '.ajagent', 'deepseek', 'fake', first.session.id)
    second = runner(tmp_path, [final()], session=session)
    assert second.run('continue').status == 'unknown_effect'
    assert second.state.status == 'waiting_for_user' and second.budget.tool_calls == 0
    third = runner(tmp_path, [final('Inspected existing file')], session=session)
    assert third.run('continue', acknowledge_unknown=True).status == 'completed'
    assert (tmp_path / 'demo.txt').read_text() == 'one'


def test_session_permission_persists_but_new_session_needs_approval(tmp_path):
    first = runner(tmp_path, [call('write_file', {'path': 'demo.txt', 'content': 'one'}), final()], ask=lambda _: 'session')
    first.run('write')
    session = Session(tmp_path / '.ajagent', 'deepseek', 'fake', first.session.id)
    second = runner(tmp_path, [call('write_file', {'path': 'demo.txt', 'content': 'two'}), final()], session=session)
    assert second.run('update').status == 'completed'
    assert (tmp_path / 'demo.txt').read_text() == 'two'
    fresh = runner(tmp_path, [call('write_file', {'path': 'demo.txt', 'content': 'three'}), final()])
    assert fresh.run('update').status == 'permission_required'
    assert (tmp_path / 'demo.txt').read_text() == 'two'
