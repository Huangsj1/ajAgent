"""User corrections are candidates; only explicit user approval creates memory."""
import json
from pathlib import Path
from uuid import uuid4
from ajagent.tools.common.persistence import identifier, save_json
from ajagent.memory.long_term import Memory


class Feedback:
    def __init__(self, root, task_id, memory_options):
        self.root = Path(root)
        self.directory = self.root / 'tasks' / identifier(task_id)
        if not (self.directory / 'tasks.json').is_file():
            raise ValueError('Unknown task')
        self.task_id, self.options = task_id, memory_options

    def list(self):
        return [json.loads(path.read_text()) for path in sorted((self.directory / 'feedback').glob('*.json'))]

    def submit(self, text):
        if not text.strip() or len(text) > self.options.text_chars:
            raise ValueError('Feedback is empty or exceeds the memory text limit')
        candidate = {'id':uuid4().hex, 'text':text, 'status':'pending', 'source':'user', 'task_id':self.task_id}
        save_json(self.directory / 'feedback' / f"{candidate['id']}.json", candidate)
        return candidate

    def review(self, candidate_id, accept):
        path = self.directory / 'feedback' / f'{identifier(candidate_id)}.json'
        candidate = json.loads(path.read_text())
        if candidate['status'] != 'pending':
            raise ValueError('Feedback was already reviewed')
        if accept:
            memory = Memory(self.root, self.options)
            source = f'task:{self.task_id}:feedback:{candidate_id}'
            # Recover safely if memory was saved before the candidate status write.
            existing = next((entry for entry in memory.list() if entry['source'] == source), None)
            candidate['memory_id'] = existing['id'] if existing else memory.remember(candidate['text'], source)
        candidate['status'] = 'accepted' if accept else 'rejected'
        save_json(path, candidate)
        return candidate
