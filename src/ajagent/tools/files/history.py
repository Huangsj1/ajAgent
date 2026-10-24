"""File-only rollback with hashes; never reverse arbitrary process side effects."""
import difflib
from hashlib import sha256
import json
import os
import stat
from uuid import uuid4
from ajagent.tools.common.persistence import identifier, save_json
from ajagent.tools.common.base import ToolError


def replace_file(target, content):
    """Replace atomically so interruption cannot leave a half-written file."""
    temporary = target.with_name(f'.{target.name}.{uuid4().hex}.tmp')
    try:
        temporary.write_bytes(content)
        if target.exists():
            os.chmod(temporary, stat.S_IMODE(target.stat().st_mode))
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


class FileHistory:
    def __init__(self, workspace):
        self.workspace = workspace
        self.root = workspace.root / '.ajagent' / 'changes'

    def prepare(self, path, content):
        target = self.workspace.resolve(path)
        exists = target.exists()
        before = target.read_bytes() if exists else b''
        after = content.encode('utf-8')
        change_id = uuid4().hex
        directory = self.root / change_id
        directory.mkdir(parents=True)
        (directory / 'before.bin').write_bytes(before)
        data = {'id':change_id, 'path':path, 'existed':exists, 'before_sha256':sha256(before).hexdigest(),
                'after_sha256':sha256(after).hexdigest(), 'status':'pending'}
        save_json(directory / 'change.json', data)
        diff = ''.join(difflib.unified_diff(before.decode('utf-8').splitlines(True), content.splitlines(True),
                                          fromfile=path, tofile=path))
        (directory / 'change.diff').write_text(diff, encoding='utf-8')
        return data

    def applied(self, data):
        data['status'] = 'applied'
        save_json(self.root / data['id'] / 'change.json', data)

    def restore(self, change_id):
        directory = self.root / identifier(change_id)
        data = json.loads((directory / 'change.json').read_text())
        if data['status'] != 'applied':
            raise ToolError('invalid_change', 'Only applied changes can be restored')
        target = self.workspace.resolve(data['path'])
        if not target.is_file() or sha256(target.read_bytes()).hexdigest() != data['after_sha256']:
            raise ToolError('rollback_conflict', 'Current file differs from this change; inspect newer edits first')
        before = (directory / 'before.bin').read_bytes()
        if sha256(before).hexdigest() != data['before_sha256']:
            raise ToolError('backup_corrupt', 'Backup hash mismatch')
        if data['existed']:
            replace_file(target, before)
        else:
            target.unlink()
        data['status'] = 'restored'
        save_json(directory / 'change.json', data)
        return {'path':data['path'], 'change_id':change_id, 'status':'restored'}, []
