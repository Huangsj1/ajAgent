"""Sanitized observability, artifacts and atomic checkpoint replacement."""
import json
from pathlib import Path
import re
from datetime import datetime, timezone
from uuid import uuid4
from threading import Lock


def redactor(secrets=()):
    secrets = tuple(s for s in secrets if isinstance(s, str) and s)
    def clean(value):
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, '[REDACTED]')
            return re.sub(r'\b(?:tvly-|sk-)[A-Za-z0-9_-]{8,}', '[REDACTED]', value)
        if isinstance(value, dict):
            return {k: '[REDACTED]' if k.lower() in {'api_key', 'authorization', 'password', 'access_token'} else clean(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        return value
    return clean


class Events:
    def __init__(self, root, clean):
        self.lock = Lock()
        self.id = uuid4().hex
        self.root = Path(root) / 'runs' / self.id
        self.root.mkdir(parents=True)
        self.clean = clean

    def emit(self, event, step=0, call_id=None, **details):
        row = self.clean(dict(event=event, run_id=self.id, step=step, call_id=call_id,
                              time=datetime.now(timezone.utc).isoformat(), **details))
        with self.lock, (self.root / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    def checkpoint(self, **data):
        path = self.root / 'checkpoint.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.clean(data), ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)

    def artifact(self, data):
        directory = self.root / 'artifacts'
        directory.mkdir(exist_ok=True)
        path = directory / f'{uuid4().hex}.json'
        path.write_text(json.dumps(self.clean(data), ensure_ascii=False, indent=2), encoding='utf-8')
        return path
