"""Durable verification, interruption, rollback and reviewed feedback."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from ajagent.tasks.progress import TaskProgress
from ajagent.tasks.feedback import Feedback
from ajagent.tools.tasks.tracking import task_tools
from ajagent.tools.common.base import ToolError
from ajagent.memory.long_term import Memory
from test_harness import runner, call, final


def objectives():
    return [{'id':str(i), 'title':f'Feature {i}', 'acceptance':f'{i}.txt contains {i}', 'paths':[f'{i}.txt'],
             'checks':[['python','-c', f"from pathlib import Path; assert Path('{i}.txt').read_text() == '{i}'"]]}
            for i in range(1,4)]


def attach(run, task_id=None):
    store = TaskProgress(run, task_id=task_id)
    run.registry.update({tool.name:tool for tool in task_tools(store)})
    run.hooks.before_finish.append(store.before_finish)
    return store


def test_three_segments_process_exit_and_resume(tmp_path):
    # A real subprocess exits while objective 2 is running, releasing its OS lock.
    script = '''
import os, sys
from pathlib import Path
from ajagent.tools.files.workspace import Workspace
from ajagent.tools.execution.runtime import Execution
from ajagent.memory.session import Session
from ajagent.context.builder import ContextBuilder
from ajagent.harness.runner import Runner
from ajagent.harness.trace import FakeModel
from ajagent.tasks.progress import TaskProgress
root=Path(sys.argv[1])
execution=Execution(Workspace(root, root/'config.local.toml'))
run=Runner(FakeModel([]), {t.name:t for t in execution.specs()}, Session(root/'.ajagent','fake','fake'), ContextBuilder(32000,4096),execution,ask=lambda _: 'once')
store=TaskProgress(run)
tasks=__TASKS__
store.create('Three segments', tasks)
store.start('1')
execution.write_file('1.txt','1')
assert store.verify('First feature tested')[0]['status']=='passed'
store.start('2')
execution.write_file('2.txt','incomplete')
store.checkpoint('First passed; second partially written','Repair 2.txt, then verify second and implement third')
(root/'task-id.txt').write_text(store.data['id'])
os._exit(0)
'''.replace('__TASKS__', repr(objectives()))
    subprocess.run([sys.executable,'-c',script,str(tmp_path)], check=True)
    run = runner(tmp_path, [], ask=lambda _: 'once')
    task_id = (tmp_path / 'task-id.txt').read_text()
    store = attach(run, task_id)
    try:
        assert [t['status'] for t in store.data['tasks']] == ['passed','running','pending']
        assert 'Repair 2.txt' in store.context()
        with pytest.raises(ToolError, match='active objective'):
            store.start('1')
        assert store.verify('Not ready')[0]['status'] == 'failed'
        run.execution.write_file('2.txt','2')
        assert store.verify('Repaired and tested')[0]['status'] == 'passed'
        store.start('3')
        run.execution.write_file('3.txt','3')
        assert store.verify('Tested third')[0]['status'] == 'passed'
        assert store.before_finish('Done') is None
        first = store.data['tasks'][0]
        assert len(first['evidence']) == 1  # First task was not re-executed.
    finally:
        store.close()


def test_changed_files_invalidate_pass_and_failure_cannot_finish(tmp_path):
    run = runner(tmp_path, [], ask=lambda _: 'once')
    store = attach(run)
    try:
        store.create('Test', objectives()[:1])
        store.start('1')
        run.execution.write_file('1.txt','1')
        assert store.verify('OK')[0]['status'] == 'passed'
        with pytest.raises(ToolError, match='unchanged'):
            store.start('1')
        run.execution.write_file('1.txt','wrong')
        assert store.status()[0]['tasks'][0]['status'] == 'pending'
        assert 'unfinished' in store.before_finish('All done')
        store.start('1')
        assert store.verify('Claimed done')[0]['status'] == 'failed'
        assert store.data['tasks'][0]['evidence'][0]['result']['exit_code'] != 0
    finally:
        store.close()


def test_rollback_detects_conflict_and_restores_existing_or_new_file(tmp_path):
    run = runner(tmp_path, [])
    (tmp_path/'existing.txt').write_text('original')
    one,_ = run.execution.write_file('existing.txt','changed')
    (tmp_path/'existing.txt').write_text('external edit')
    with pytest.raises(ToolError, match='differs'):
        run.execution.history.restore(one['change_id'])
    (tmp_path/'existing.txt').write_text('changed')
    run.execution.history.restore(one['change_id'])
    assert (tmp_path/'existing.txt').read_text() == 'original'
    two,_ = run.execution.write_file('new.txt','new')
    run.execution.history.restore(two['change_id'])
    assert not (tmp_path/'new.txt').exists()


def test_feedback_is_not_memory_until_user_approves(tmp_path):
    run = runner(tmp_path, [])
    store = attach(run)
    try:
        store.create('Test', objectives()[:1])
        feedback = Feedback(tmp_path/'.ajagent', store.data['id'], run.all_options.memory)
        candidate = feedback.submit('Use unittest for this project')
        memory = Memory(tmp_path/'.ajagent', run.all_options.memory)
        assert memory.list() == []
        assert candidate['text'] in store.context()
        feedback.review(candidate['id'], True)
        assert memory.list()[0]['text'] == candidate['text']
        with pytest.raises(ValueError, match='already reviewed'):
            feedback.review(candidate['id'], True)
    finally:
        store.close()


def test_task_lock_and_fresh_context_preserve_unknown_effect_guard(tmp_path):
    run = runner(tmp_path, [])
    store = attach(run)
    store.create('Test', objectives()[:1])
    store.start('1')
    run.pending = [{'tool':'run_command','arguments':{},'status':'unknown'}]
    run.save()
    task_id = store.data['id']
    with pytest.raises(BlockingIOError):
        attach(runner(tmp_path, []), task_id)
    store.close()
    resumed = runner(tmp_path, [final('Unsafe')])
    recovery = attach(resumed, task_id)
    try:
        assert resumed.run('Continue').status == 'unknown_effect'
    finally:
        recovery.close()


def test_schema_error_explains_limit_without_echoing_input(tmp_path):
    from ajagent.tools.common.registry import execute
    from ajagent.core.types import ToolCall
    run = runner(tmp_path, [])
    value = {'path':'private-secret-value','length':4000}
    result = execute(ToolCall('x','read_artifact',json.dumps(value)), run.registry)
    assert not result.ok and 'length' in result.error['message'] and '2000' in result.error['message']
    assert 'private-secret-value' not in result.error['message']


def test_incomplete_text_survives_but_partial_tool_calls_never_execute(tmp_path):
    turn = final('Useful partial analysis')
    turn['stop_reason'] = 'incomplete'
    run = runner(tmp_path, [turn])
    result = run.run('Analyze')
    assert result.status == 'incomplete' and 'Useful partial analysis' in result.answer
    truncated = call('write_file', {'path':'unsafe.txt','content':'partial'})
    truncated['stop_reason'] = 'incomplete'
    run = runner(tmp_path, [truncated], ask=lambda _: 'once')
    assert run.run('Write').status == 'incomplete'
    assert not (tmp_path/'unsafe.txt').exists()


def test_task_checkpoint_replaces_context_not_history_or_usage(tmp_path):
    turns = [call('plan_tasks', {'goal':'Check one feature','tasks':objectives()[:1]}),
             call('start_task', {'task_id':'1'}),
             call('write_file', {'path':'1.txt','content':'1'}),
             call('verify_task', {'summary':'Verified using the declared assertion'}), final('Completed')]
    run = runner(tmp_path, turns, ask=lambda _: 'once')
    store = attach(run)
    try:
        result = run.run('Create the feature')
        assert result.status == 'completed'
        assert run.builder.covered > 0 and 'Durable task state' in run.builder.summary
        assert run.session.group_count > run.builder.covered
        assert run.session.groups.start == run.builder.covered
        assert result.usage == {'input_tokens':60,'output_tokens':11}
        assert 'write_file' in run.session.path.read_text()
        events = [json.loads(row) for row in (run.events.root/'events.jsonl').read_text().splitlines()]
        assert any(row['event']=='task_context_checkpoint' for row in events)
    finally:
        store.close()


def test_command_output_retains_failure_tail(tmp_path):
    from ajagent.tools.execution.runtime import command
    data,_ = command(['python','-c',"print('BEGIN'); print('x'*1000); print('ASSERTION FAILED AT END')"],tmp_path,output_limit=100)
    assert len(data['output']) == 100
    assert data['output'].startswith('BEGIN') and data['output'].endswith('ASSERTION FAILED AT END\n')
    assert data['truncated'] and data['output_layout'] == 'head_tail'


def test_configured_approval_still_respects_denials(tmp_path):
    from dataclasses import replace
    from ajagent.options import DEFAULTS
    options=replace(DEFAULTS,harness=replace(DEFAULTS.harness,auto_approve_tools=('write_file',)))
    run=runner(tmp_path,[call('write_file',{'path':'ok.txt','content':'ok'}),final()], options=options)
    assert run.run('Write').status=='completed'
    assert (tmp_path/'ok.txt').exists()
    denied=runner(tmp_path,[call('write_file',{'path':'no.txt','content':'no'}),final()],options=options,denied=['write_file'])
    denied.run('Write')
    assert not (tmp_path/'no.txt').exists()


def test_layout_change_keeps_acceptance_and_checks(tmp_path):
    run=runner(tmp_path,[],ask=lambda _: 'once')
    store=attach(run)
    try:
        store.create('Split outputs',objectives()[:1])
        store.start('1')
        checks=store.data['tasks'][0]['checks']
        store.update_paths('1',['1.txt','split.txt'],'Split into implementation and supporting file')
        assert store.data['tasks'][0]['checks'] == checks
        run.execution.write_file('1.txt','1')
        run.execution.write_file('split.txt','support')
        assert store.verify('Checks unchanged and passed')[0]['status']=='passed'
        with pytest.raises(ToolError):
            store.update_paths('1',['1.txt'],'Remove dependency')
    finally:
        store.close()


def test_acceptance_can_generate_required_artifacts(tmp_path):
    run=runner(tmp_path,[],ask=lambda _: 'once')
    store=attach(run)
    try:
        task=objectives()[0]
        task['checks']=[['python','-c',"from pathlib import Path; Path('1.txt').write_text('1')"]]
        store.create('Generate output',[task])
        store.start('1')
        assert store.verify('Generated artifact')[0]['status']=='passed'
    finally:
        store.close()


def test_idle_feedback_does_not_reset_budget_or_mark_progress(tmp_path):
    run=runner(tmp_path,[])
    store=attach(run)
    try:
        store.create('Work',objectives()[:1])
        assert store.before_step(1) is None
        message=store.before_step(1+store.options.max_idle_steps)
        assert 'repeated reads' in message
        assert store.data['tasks'][0]['status']=='pending'
        assert run.used=={'input_tokens':0,'output_tokens':0}
    finally:
        store.close()
