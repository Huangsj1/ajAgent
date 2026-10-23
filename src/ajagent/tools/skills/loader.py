"""Small, explicit skill loader using a restricted name/description front matter."""
from pathlib import Path
import re
from ajagent.options import DEFAULTS


class Skills:
    def __init__(self, root, options=DEFAULTS.capabilities):
        self.root, self.options = Path(root), options
        self.entries = {}
        for path in sorted(self.root.glob('*/SKILL.md')):
            if path.is_symlink() or path.parent.is_symlink():
                continue
            # Read only the metadata during discovery.
            with path.open(encoding='utf-8') as stream:
                if stream.readline().strip() != '---':
                    raise ValueError(f'Missing skill metadata: {path.name}')
                metadata = {}
                size = 0
                for line in stream:
                    size += len(line)
                    if size > options.skill_metadata_chars:
                        raise ValueError('Skill metadata too large')
                    if line.strip() == '---':
                        break
                    key, separator, value = line.partition(':')
                    if not separator or key.strip() not in {'name', 'description'}:
                        raise ValueError('Skills support only name and description metadata')
                    metadata[key.strip()] = value.strip().strip('"\'')
                else:
                    raise ValueError('Unclosed skill metadata')
            name = metadata.get('name', '')
            if not re.fullmatch(r'[a-z][a-z0-9_-]*', name) or not metadata.get('description') or name in self.entries:
                raise ValueError('Invalid or duplicate skill name/description')
            self.entries[name] = {**metadata, 'path': path}

    def catalog(self):
        return '\n'.join(f"- {name}: {entry['description']}" for name, entry in self.entries.items())

    def expand(self, prompt):
        """Only a leading user command loads a body; tool output cannot activate it."""
        match = re.match(r'^/skill\s+(\S+)(?:\s+([\s\S]*))?$', prompt)
        if not match:
            return prompt
        name, task = match.groups()
        if name not in self.entries:
            raise ValueError(f'Unknown skill: {name}')
        body = self.load(name)[0]['instructions']
        return f'Skill guidance ({name}; existing permissions still apply):\n{body}\n\nTask:\n{task or "Follow the skill."}'

    def load(self, name):
        from ajagent.tools.common.base import ToolError
        if name not in self.entries:
            raise ToolError('unknown_skill', 'Choose an advertised skill name')
        path = self.entries[name]['path']
        with path.open('rb') as stream:
            content = stream.read(self.options.skill_bytes + 1)
        if len(content) > self.options.skill_bytes:
            raise ValueError('Skill body too large')
        body = content.decode('utf-8').split('---', 2)[2].strip()
        return {'name': name, 'instructions': body,
                'note': 'Guidance only; tool permissions and task contracts still apply.'}, [
                    {'uri': str(path), 'kind': 'skill'}]

    def spec(self):
        from ajagent.tools.common.base import ToolSpec, schema
        return ToolSpec('load_skill', 'Load full guidance for an available skill when useful. This grants no tool permissions.',
                        schema({'name': {'type': 'string', 'enum': list(self.entries)}}, ['name']), self.load)
