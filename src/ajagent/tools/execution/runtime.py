"""Workspace writes and bounded subprocesses. This is not a security sandbox."""
import difflib
import os
import selectors
import signal
import subprocess
import time
import sys
from ajagent.tools.files.history import FileHistory, replace_file
from ajagent.options import DEFAULTS, RUNTIME
from ajagent.tools.common.base import ToolError, ToolSpec, schema


def command(argv, cwd, timeout=RUNTIME.tool_timeout, output_limit=None, options=DEFAULTS.tools, *, separate=False, python_script=False):
    output_limit = options.command_output_bytes if output_limit is None else output_limit
    if not argv or (argv[0] not in options.allowed_commands and not (python_script and argv[0] == sys.executable)):
        raise ToolError('forbidden', 'Allowed executables: ' + ', '.join(options.allowed_commands))
    # No shell; descendants share a process group for cancellation and timeout.
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE if separate else subprocess.STDOUT,
                               start_new_session=True)
    output = {'stdout': bytearray(), 'stderr': bytearray()}
    tails = {'stdout': bytearray(), 'stderr': bytearray()}
    capacity = output_limit // 2 if separate else output_limit
    truncated = False
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
    if separate:
        selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise ToolError('timeout', 'Command timed out; process group terminated')
            for key, _ in selector.select(min(options.command_poll_seconds, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), options.command_chunk_bytes)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                # Keep the start and the end: test failures are often at the end.
                head, tail = output[key.data], tails[key.data]
                room = max(0, capacity // 2 - len(head))
                head.extend(chunk[:room])
                tail.extend(chunk[room:])
                tail_limit = capacity - capacity // 2
                if len(tail) > tail_limit:
                    del tail[:len(tail) - tail_limit]
                    truncated = True
        process.wait(timeout=max(.001, deadline - time.monotonic()))
        decoded = {key: (value + tails[key]).decode('utf-8', errors='replace') for key, value in output.items()}
        streams = decoded if separate else {'output': decoded['stdout']}
        return {'exit_code': process.returncode, **streams, 'truncated': truncated,
                'output_layout': 'head_tail' if truncated else 'complete'}, []
    except subprocess.TimeoutExpired:
        raise ToolError('timeout', 'Command timed out; process group terminated') from None
    finally:
        # Also terminate descendants that closed their inherited output pipe.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        selector.close()
        process.stdout.close()
        if process.stderr:
            process.stderr.close()


class Execution:
    def __init__(self, workspace):
        self.workspace = workspace
        self.options = workspace.options
        self.timeout = workspace.timeout
        self.history = FileHistory(workspace)

    def preview(self, path, content):
        """Preview the diff of a workspace file write"""
        target = self.workspace.resolve(path)
        if target.exists() and (not target.is_file() or target.stat().st_size > self.options.max_file_bytes):
            raise ToolError('too_large', f'Target must be a file of at most {self.options.max_file_bytes} bytes')
        before = target.read_text(encoding='utf-8') if target.exists() else ''
        diff = ''.join(difflib.unified_diff(before.splitlines(True), content.splitlines(True),
                                          fromfile=path, tofile=path))
        return {'path': path, 'before_chars': len(before), 'after_chars': len(content),
                'diff': diff[:self.options.diff_chars], 'diff_truncated': len(diff) > self.options.diff_chars}

    def write_file(self, path, content):
        change = self.preview(path, content)
        target = self.workspace.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        backup = self.history.prepare(path, content)
        replace_file(target, content.encode('utf-8'))
        self.history.applied(backup)
        change['change_id'] = backup['id']
        return change, [{'path': path}]

    def run_command(self, argv, directory='.'):
        cwd = self.workspace.resolve(directory)
        if not cwd.is_dir():
            raise ToolError('not_found', 'Working directory does not exist')
        return command(argv, cwd, self.timeout, options=self.options)

    def run_python(self, path, args=None):
        target = self.workspace.resolve(path)
        if target.suffix != '.py' or not target.is_file():
            raise ToolError('invalid_script', 'Choose an existing workspace .py file')
        data, _ = command([sys.executable, '-B', str(target), *(args or [])], self.workspace.root,
                          self.timeout, options=self.options, separate=True, python_script=True)
        return data, [{'path': path, 'kind': 'script'}]

    def specs(self):
        return [ToolSpec('restore_file', 'Restore one file-tool write only when its current hash still matches. Does not undo commands or external changes.',
                         schema({'change_id': {'type':'string', 'pattern':'^[a-f0-9]{32}$'}}, ['change_id']), self.history.restore, 'execute'),
                ToolSpec('run_python', 'Execute an approved workspace Python script in a subprocess (not a sandbox).', schema({
            'path': {'type': 'string', 'maxLength': self.options.path_chars},
            'args': {'type': 'array', 'maxItems': self.options.command_max_args,
                     'items': {'type': 'string', 'maxLength': self.options.command_arg_chars}}}, ['path']), self.run_python, 'execute'),
            ToolSpec('write_file', 'Create or replace a UTF-8 workspace file after permission.', schema({
            'path': {'type': 'string', 'minLength': 1, 'maxLength': self.options.path_chars},
            'content': {'type': 'string', 'maxLength': self.options.write_chars}}, ['path', 'content']), self.write_file, 'write'),
            ToolSpec('run_command', 'Run an approved argv list in the workspace (not a sandbox).', schema({
                'argv': {'type': 'array', 'minItems': 1, 'maxItems': self.options.command_max_args,
                         'items': {'type': 'string', 'maxLength': self.options.command_arg_chars}},
                'directory': {'type': 'string', 'maxLength': self.options.path_chars}}, ['argv']), self.run_command, 'execute')]
