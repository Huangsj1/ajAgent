"""One active objective, executable acceptance checks and durable handoffs."""
from copy import deepcopy
from hashlib import sha256
import json
from uuid import uuid4
import fcntl
from ajagent.tasks.plan import TaskOptions
from ajagent.tools.common.persistence import identifier, save_json
from ajagent.core.types import ToolCall
from ajagent.tools.common.base import ToolError


class TaskProgress:
    def __init__(self, runner, options=TaskOptions(), task_id=None):
        self.runner, self.options = runner, options
        self.workspace = runner.execution.workspace
        self.root = self.workspace.root / '.ajagent' / 'tasks'
        self.data = self.directory = self.lock = None
        self.last_revision = self.last_progress_step = 0
        if task_id:
            try:
                self.bind(task_id)
                self.refresh()
            except BaseException:
                self.close()
                raise

    def bind(self, task_id):
        directory = self.root / identifier(task_id)
        if self.data is not None:
            raise ToolError('task_bound', 'This session already has a task; resume or start a new session')
        directory.mkdir(parents=True, exist_ok=True)
        lock = (directory / '.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.data = json.loads((directory / 'tasks.json').read_text())
        except BaseException:
            lock.close()
            raise
        self.directory, self.lock = directory, lock
        if not self.runner.previous_run and self.data.get('run_id'):
            self.runner.previous_run = self.data['run_id']
        self.runner.session.task_id = task_id
        self.runner.session.append({'event':'task_bound', 'task_id':task_id})
        self.runner.session.flush()

    def close(self):
        if self.lock:
            self.lock.close()
            self.lock = None

    def create(self, goal, tasks):
        if self.data:
            raise ToolError('task_bound', 'A plan already exists; use task_status')
        if not goal.strip() or len(goal) > self.options.text_chars or not 1 <= len(tasks) <= self.options.max_tasks:
            raise ToolError('invalid_plan', 'Provide a bounded goal and nonempty task list')
        ids = set()
        for task in tasks:
            key = identifier(task['id'])
            if key in ids or not task['acceptance'].strip() or not task['title'].strip():
                raise ToolError('invalid_plan', 'Tasks need unique IDs and acceptance criteria')
            ids.add(key)
            for path in task['paths']:
                self.workspace.resolve(path)
            if not task['paths'] or not task['checks']:
                raise ToolError('invalid_plan', 'Each task needs output paths and executable checks')
            for argv in task['checks']:
                if not argv or argv[0] not in self.workspace.options.allowed_commands:
                    raise ToolError('invalid_plan', 'Checks must use allowed executables')
            task.update(status='pending', evidence=[], hashes={}, summary='')
        task_id = uuid4().hex
        directory = self.root / task_id
        data = {'id':task_id, 'goal':goal, 'tasks':tasks, 'active':None, 'next_step':'Start the first pending task',
                'summary':'', 'revision':0, 'feedback':[]}
        save_json(directory / 'tasks.json', self.runner.clean(data))
        self.bind(task_id)
        self.save()
        return self.status()

    def require(self):
        if self.data is None:
            raise ToolError('no_task', 'Create a plan with plan_tasks first')

    def hashes(self, task, *, allow_missing=False):
        result = {}
        for path in task['paths']:
            target = self.workspace.resolve(path)
            if allow_missing and not target.exists():
                continue
            if not target.is_file():
                raise ToolError('missing_output', f'Required output missing: {path}')
            if target.stat().st_size > self.workspace.options.max_file_bytes:
                raise ToolError('too_large', f'Output exceeds file limit: {path}')
            result[path] = sha256(target.read_bytes()).hexdigest()
        return result

    def refresh(self):
        self.require()
        changed = False
        for task in self.data['tasks']:
            if task['status'] == 'passed':
                try:
                    valid = self.hashes(task) == task['hashes']
                except ToolError:
                    valid = False
                if not valid:
                    task.update(status='pending', summary='Previously verified outputs changed; recheck required')
                    changed = True
        if changed:
            self.save()

    def status(self):
        self.refresh()
        data = deepcopy(self.data)
        for task in data['tasks']:
            task['evidence'] = [{key:value for key,value in item.items() if key != 'result'} for item in task['evidence']]
        return data, []

    def start(self, task_id):
        self.refresh()
        if self.data['active'] and self.data['active'] != task_id:
            raise ToolError('active_task', 'Finish the active objective before switching')
        task = self.find(task_id)
        if task['status'] == 'passed':
            raise ToolError('already_passed', 'Verified outputs are unchanged; do not repeat this objective')
        task['status'] = 'running'
        self.data['active'] = task_id
        self.save()
        return task, []

    def find(self, task_id):
        self.require()
        for task in self.data['tasks']:
            if task['id'] == task_id:
                return task
        raise ToolError('unknown_task', 'Unknown objective ID')

    def update_paths(self, task_id, paths, reason):
        self.refresh()
        task = self.find(task_id)
        if task['status'] == 'passed':
            raise ToolError('already_passed', 'Cannot change a verified objective')
        if not paths or not reason.strip():
            raise ToolError('invalid_plan', 'Provide output paths and the layout-change reason')
        for path in paths:
            self.workspace.resolve(path)
        task['paths'] = list(dict.fromkeys(paths))
        task['layout_reason'] = reason
        self.save()
        return task, []

    def checkpoint(self, summary, next_step):
        self.require()
        self.data.update(summary=summary, next_step=next_step)
        self.save()
        if self.options.compact_on_checkpoint:
            self.runner.context_handoff = self.context
        return {'task_id':self.data['id'], 'revision':self.data['revision'], 'active':self.data['active']}, []

    def verify(self, summary):
        self.require()
        task = self.find(self.data['active'])
        before = self.hashes(task, allow_missing=True)
        evidence = []
        for argv in task['checks']:
            call = ToolCall(uuid4().hex, 'run_command', json.dumps({'argv':argv}))
            result = self.runner.execute(call)
            self.runner.session.append({'event':'task_check', 'task_id':task['id'], 'call_id':call.call_id,
                                        'result':self.runner.clean(vars(result))})
            evidence.append({'call_id':call.call_id, 'run_id':self.runner.events.id, 'argv':argv,
                             'ok':result.ok and result.data.get('exit_code') == 0,
                             'result':result.data if result.ok else result.error})
            if not evidence[-1]['ok']:
                break
        after = self.hashes(task)
        passed = len(evidence) == len(task['checks']) and all(e['ok'] for e in evidence) and all(after.get(path) == digest for path, digest in before.items())
        task.update(status='passed' if passed else 'failed', evidence=evidence, hashes=after if passed else {}, summary=summary)
        self.data['active'] = None if passed else task['id']
        self.data['next_step'] = 'Choose the next pending objective' if passed else 'Repair the failed check and verify again'
        self.save()
        if passed and self.options.compact_on_checkpoint:
            self.runner.context_handoff = self.context
        return {'task_id':task['id'], 'status':task['status'], 'evidence':evidence}, []

    def observe(self, call, args, result):
        if result.ok and call.name in {'write_file', 'apply_proposal', 'restore_file'}:
            self.last_progress_step = self.runner.step

    def before_step(self, step):
        if not self.data:
            return None
        if self.data['revision'] != self.last_revision:
            self.last_revision = self.data['revision']
            self.last_progress_step = step
        if step - self.last_progress_step >= self.options.max_idle_steps:
            self.last_progress_step = step
            return ('No file-tool change or task-state update in the last work segment. '
                    'Use existing evidence; avoid repeated reads or unnecessary refactoring. '
                    'Run the next acceptance check, make a targeted fix, or checkpoint a concrete blocker. '
                    f"Active objective: {self.data['active']}. Next: {self.data['next_step']}")
        return None

    def before_finish(self, answer):
        if not self.data:
            return None
        self.refresh()
        remaining = [t['id'] for t in self.data['tasks'] if t['status'] != 'passed']
        return 'Plan unfinished; continue work and verification: ' + ', '.join(remaining) if remaining else None

    def context(self):
        if not self.data:
            return ''
        self.refresh()
        # Full check outputs remain on disk; only bounded progress is handed off.
        data = {key:value for key,value in self.data.items() if key != 'tasks'}
        from ajagent.tasks.feedback import Feedback
        data['feedback'] = [row for row in Feedback(self.workspace.root / '.ajagent', self.data['id'], self.runner.all_options.memory).list() if row['status'] != 'rejected']
        data['current_request'] = getattr(self.runner, 'current_request', '')
        data['tasks'] = [{key:value for key,value in task.items() if key != 'evidence'} for task in self.data['tasks']]
        return '\nDurable task state (data):\n' + json.dumps(self.runner.clean(data), ensure_ascii=False)

    def save(self):
        self.require()
        self.data['revision'] += 1
        self.data['run_id'] = self.runner.events.id
        self.data['session_id'] = self.runner.session.id
        cleaned = self.runner.clean(self.data)
        # JSON is authoritative. Snapshots and Markdown are readable projections.
        save_json(self.directory / 'tasks.json', cleaned)
        save_json(self.directory / 'checkpoints' / f"{self.data['revision']:06d}.json", cleaned)
        lines = [f"# {cleaned['goal']}", '', cleaned['summary'], '', f"Next: {cleaned['next_step']}", '']
        for task in cleaned['tasks']:
            lines += [f"- {task['id']} [{task['status']}]: {task['title']}", f"  Acceptance: {task['acceptance']}", f"  Result: {task['summary']}"]
        (self.directory / 'progress.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
