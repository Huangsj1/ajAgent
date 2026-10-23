"""Child-authored proposals, reviewed and applied serially by the parent."""
import ast
import difflib
from hashlib import sha256
import json
from pathlib import Path
import tomllib
from ajagent.tools.common.base import ToolError, ToolSpec, schema


def digest(data):
    return sha256(data).hexdigest()


def syntax_check(path, content):
    try:
        if path.endswith('.py'):
            ast.parse(content, filename=path)
        elif path.endswith('.json'):
            json.loads(content)
        elif path.endswith('.toml'):
            tomllib.loads(content)
    except (SyntaxError, ValueError) as error:
        raise ToolError('syntax_error', f'Proposed {path} is invalid: {type(error).__name__}') from None


class Proposals:
    def __init__(self, runner, contract=None):
        self.runner, self.contract = runner, contract
        self.workspace = runner.execution.workspace
        self.created = []

    def propose(self, path, base_sha256, content, rationale):
        if len(self.created) >= self.runner.all_options.capabilities.max_proposals:
            raise ToolError('proposal_limit', 'Child proposal limit reached')
        target = self.workspace.resolve(path)
        allowed = [self.workspace.resolve(item) for item in self.contract['allowed_paths']]
        if not any(target == root or target.is_relative_to(root) for root in allowed):
            raise ToolError('contract_scope', 'Proposed path is outside the task contract')
        if target.exists() and target.stat().st_size > self.workspace.options.max_file_bytes:
            raise ToolError('too_large', 'Proposal target exceeds the reading limit')
        before = target.read_bytes() if target.exists() else b''
        if digest(before) != base_sha256:
            raise ToolError('stale_proposal', 'Base hash does not match current file; read it again')
        syntax_check(path, content)
        proposal = {'kind': 'change_proposal', 'path': path, 'base_sha256': base_sha256,
                    'content': content, 'rationale': rationale, 'contract': self.contract,
                    'diff': ''.join(difflib.unified_diff(before.decode('utf-8').splitlines(True),
                                  content.splitlines(True), fromfile=path, tofile=path))}
        artifact = str(self.runner.events.artifact(proposal))
        self.created.append(artifact)
        return {'artifact': artifact, 'path': path, 'status': 'proposed'}, []

    def read(self, artifact):
        target = Path(artifact)
        root = self.runner.events.root.parent.resolve()
        if target.is_symlink() or not target.resolve().is_relative_to(root) or target.parent.name != 'artifacts':
            raise ToolError('forbidden', 'Choose a saved proposal artifact')
        if target.stat().st_size > self.workspace.options.max_file_bytes:
            raise ToolError('too_large', 'Proposal artifact is too large')
        data = json.loads(target.read_text(encoding='utf-8'))
        if data.get('kind') != 'change_proposal':
            raise ToolError('invalid_proposal', 'Artifact is not a change proposal')
        target = self.workspace.resolve(data['path'])
        if target.exists() and target.stat().st_size > self.workspace.options.max_file_bytes:
            raise ToolError('too_large', 'Current file is too large')
        current = target.read_bytes() if target.exists() else b''
        if digest(current) != data['base_sha256']:
            raise ToolError('proposal_conflict', 'File changed since proposal creation; rebase or create a new proposal')
        syntax_check(data['path'], data['content'])
        if len(data['content']) > self.workspace.options.write_chars:
            raise ToolError('too_large', 'Proposed content exceeds the write limit')
        return data

    def inspect(self, artifact):
        data = self.read(artifact)
        return {key: value for key, value in data.items() if key != 'content'}, []

    def apply(self, artifact, review):
        if self.runner.parent is not None:
            raise ToolError('single_writer', 'Only the parent can apply proposals')
        data = self.read(artifact)
        result, sources = self.runner.execution.write_file(data['path'], data['content'])
        result.update(artifact=artifact, review=review)
        return result, sources

    def spec(self):
        options = self.workspace.options
        return ToolSpec('propose_patch', 'Save a draft/diff artifact without modifying workspace files. Use read_file sha256; for new files use the SHA256 of empty bytes.',
                        schema({'path': {'type': 'string', 'maxLength': options.path_chars},
                                'base_sha256': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
                                'content': {'type': 'string', 'maxLength': options.write_chars},
                                'rationale': {'type': 'string', 'maxLength': options.write_chars}},
                               ['path', 'base_sha256', 'content', 'rationale']), self.propose, parallel=False)


def proposal_tools(runner):
    runner.proposals = Proposals(runner)
    artifact = {'type': 'string', 'minLength': 1, 'maxLength': runner.all_options.tools.path_chars}
    return [ToolSpec('inspect_proposal', 'Check a child proposal for stale base/syntax and read its diff. Review logical conflicts yourself before applying.',
                     schema({'artifact': artifact}, ['artifact']), runner.proposals.inspect),
            ToolSpec('apply_proposal', 'Apply one reviewed proposal serially after approval. Reject changed bases and syntax errors; explain your logical review.',
                     schema({'artifact': artifact, 'review': {'type': 'string', 'minLength': 1,
                             'maxLength': runner.all_options.capabilities.task_chars}}, ['artifact', 'review']), runner.proposals.apply, 'execute')]
