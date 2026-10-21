"""Append-only session events; complete model/tool batches are replay units."""
import json
from pathlib import Path
import re
from uuid import uuid4


class Session:
    def __init__(self, root, provider, model, session_id=None):
        self.id = session_id or uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', self.id):
            raise ValueError('Invalid session ID')
        self.path = Path(root) / 'sessions' / f'{self.id}.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pending = []
        self._pending_bytes = 0
        self.groups = []
        self.checkpoint = {}
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        if session_id:
            # Load the whole session
            events = [json.loads(line) for line in self.path.read_text().splitlines()]
            if events[0] != {'event': 'session', 'provider': provider, 'model': model}:
                raise ValueError('Resume requires the original provider and model')
            # get the last context event
            self.checkpoint = next((e for e in reversed(events) if e['event'] == 'context'), {})
            # End events contain cumulative snapshots, not increments.
            last_end = next((e for e in reversed(events) if e['event'] == 'end'), {})
            for key in self.usage:
                self.usage[key] = last_end.get('usage', {}).get(key, 0)
            # Only keep the messages
            self.groups = [event['items'] for event in events if event['event'] == 'messages']
        else:
            self.append({'event': 'session', 'provider': provider, 'model': model})

    def append(self, event):
        """Buffer immutable JSON lines; cap pending data at roughly 64 KiB."""
        line = json.dumps(event, ensure_ascii=False) + '\n'
        self._pending.append(line)
        self._pending_bytes += len(line.encode('utf-8'))
        if self._pending_bytes >= 65536:
            self.flush()

    def flush(self):
        """Write pending events together; retain the buffer if writing fails."""
        if not self._pending:
            return
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(''.join(self._pending))
        self._pending.clear()
        self._pending_bytes = 0

    def record(self, items):
        """Checkpoint each complete replay batch, not each individual event."""
        self.append({'event': 'messages', 'items': items})
        self.groups.append(items)
        self.flush()
