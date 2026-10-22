"""Workspace writes and bounded subprocesses. This is not a security sandbox."""
import difflib
import os
import selectors
import signal
import subprocess
import time
from ajagent.options import DEFAULTS, RUNTIME
from ajagent.tools.base import ToolError, ToolSpec, schema


def command(argv, cwd, timeout=RUNTIME.tool_timeout, output_limit=None, options=DEFAULTS.tools):
    output_limit = options.command_output_bytes if output_limit is None else output_limit
    if not argv or argv[0] not in options.allowed_commands:
        raise ToolError('forbidden', 'Allowed executables: ' + ', '.join(options.allowed_commands))
    # No shell; descendants share a process group for cancellation and timeout.
    process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               start_new_session=True)
    output, truncated = bytearray(), False
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
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
                room = max(0, output_limit - len(output))
                output.extend(chunk[:room])
                truncated |= len(chunk) > room
        process.wait(timeout=max(.001, deadline - time.monotonic()))
        return {'exit_code': process.returncode, 'output': output.decode('utf-8', errors='replace'),
                'truncated': truncated}, []
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


class Execution:
    def __init__(self, workspace):
        self.workspace = workspace
        self.options = workspace.options
        self.timeout = workspace.timeout

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
        target.write_text(content, encoding='utf-8')
        return change, [{'path': path}]

    def run_command(self, argv, directory='.'):
        cwd = self.workspace.resolve(directory)
        if not cwd.is_dir():
            raise ToolError('not_found', 'Working directory does not exist')
        return command(argv, cwd, self.timeout, options=self.options)

    def specs(self):
        return [ToolSpec('write_file', 'Create or replace a UTF-8 workspace file after permission.', schema({
            'path': {'type': 'string', 'minLength': 1, 'maxLength': self.options.path_chars},
            'content': {'type': 'string', 'maxLength': self.options.write_chars}}, ['path', 'content']), self.write_file, 'write'),
            ToolSpec('run_command', 'Run an approved argv list in the workspace (not a sandbox).', schema({
                'argv': {'type': 'array', 'minItems': 1, 'maxItems': self.options.command_max_args,
                         'items': {'type': 'string', 'maxLength': self.options.command_arg_chars}},
                'directory': {'type': 'string', 'maxLength': self.options.path_chars}}, ['argv']), self.run_command, 'execute')]
