"""Workspace writes and bounded subprocesses. This is not a security sandbox."""
import difflib
import os
import selectors
import signal
import subprocess
import time
from ajagent.tools.base import ToolError, ToolSpec, schema


def command(argv, cwd, timeout=10, output_limit=12000):
    if not argv or argv[0] not in {'python', 'python3', 'pytest', 'rg'}:
        raise ToolError('forbidden', 'Allowed executables: python, python3, pytest, rg')
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
            for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 8192)
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
        self.timeout = workspace.timeout

    def preview(self, path, content):
        """Preview the diff of a workspace file write"""
        target = self.workspace.resolve(path)
        if target.exists() and (not target.is_file() or target.stat().st_size > 1_000_000):
            raise ToolError('too_large', 'Target must be a file of at most 1 MB')
        before = target.read_text(encoding='utf-8') if target.exists() else ''
        diff = ''.join(difflib.unified_diff(before.splitlines(True), content.splitlines(True),
                                          fromfile=path, tofile=path))
        return {'path': path, 'before_chars': len(before), 'after_chars': len(content),
                'diff': diff[:3000], 'diff_truncated': len(diff) > 3000}

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
        return command(argv, cwd, self.timeout)

    def specs(self):
        return [ToolSpec('write_file', 'Create or replace a UTF-8 workspace file after permission.', schema({
            'path': {'type': 'string', 'minLength': 1, 'maxLength': 500},
            'content': {'type': 'string', 'maxLength': 100000}}, ['path', 'content']), self.write_file, 'write'),
            ToolSpec('run_command', 'Run an approved argv list in the workspace (not a sandbox).', schema({
                'argv': {'type': 'array', 'minItems': 1, 'maxItems': 40,
                         'items': {'type': 'string', 'maxLength': 2000}},
                'directory': {'type': 'string', 'maxLength': 500}}, ['argv']), self.run_command, 'execute')]
