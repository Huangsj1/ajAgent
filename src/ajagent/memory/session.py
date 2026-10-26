"""Append-only session events; complete model/tool batches are replay units."""
import json
from pathlib import Path
from copy import deepcopy
import re
from uuid import uuid4
from ajagent.options import DEFAULTS
from ajagent.memory.history import ActiveGroups, HistoryIndex


class Session:
    def __init__(self, root, provider, model, session_id=None, options=DEFAULTS.memory):
        self.options = options
        self.provider, self.model = provider, model
        self.id = session_id or uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', self.id):
            raise ValueError('Invalid session ID')
        self.path = Path(root) / 'sessions' / f'{self.id}.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pending = []
        self._pending_bytes = 0
        self.clean = lambda value: value
        self.grants = set()     # granted permission keys
        self.run_id = None
        self.task_id = None
        self.groups = ActiveGroups()
        self.history = HistoryIndex(self.path)
        self.checkpoint = {}
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        if session_id:
            self.history.sync()
            meta = self.history.metadata()
            if meta.get('session') != {'event': 'session', 'provider': provider, 'model': model}:
                raise ValueError('Resume requires the original provider and model')
            self.task_id = meta.get('task_bound', {}).get('task_id')
            self.grants = set(meta.get('grants', []))
            self.run_id = meta.get('harness_run', {}).get('run_id')
            self.checkpoint = meta.get('context', {})
            for key in self.usage:
                self.usage[key] = meta.get('end', {}).get('usage', {}).get(key, 0)
            start = (self.checkpoint['summarized_groups']
                     if self.history.valid_checkpoint(self.checkpoint, meta['count']) else 0)
            self.groups = ActiveGroups(self.history.groups(start), start)
        else:
            self.append({'event': 'session', 'provider': provider, 'model': model})

    def append(self, event):
        """Buffer immutable JSON lines; flush at the configured byte threshold."""
        line = json.dumps(self.clean(event), ensure_ascii=False) + '\n'
        self._pending.append(line)
        self._pending_bytes += len(line.encode('utf-8'))
        if self._pending_bytes >= self.options.session_buffer_bytes:
            self.flush()

    def flush(self):
        """Write pending events together; retain the buffer if writing fails."""
        if not self._pending:
            return
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(''.join(self._pending))
        self._pending.clear()
        self._pending_bytes = 0
        self.history.sync()

    @property
    def group_count(self):
        return self.groups.total

    def record(self, items):
        """Checkpoint each complete replay batch, not each individual event."""
        items = deepcopy(items)
        self.append({'event': 'messages', 'group_id': f'g{self.group_count+1}', 'items': items})
        self.groups.append(items)
        self.flush()

    def finish(self, result):
        """Store one cumulative usage snapshot for every termination path."""
        self.usage = dict(result.usage)
        self.append({'event': 'end', 'status': result.status, 'usage': result.usage,
                     'last_usage': result.last_usage})
