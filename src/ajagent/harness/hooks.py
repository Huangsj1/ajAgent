"""Hook points and small, opt-in completion checks."""
import re
from ajagent.options import DEFAULTS
from urllib.parse import unquote, urlsplit


class Hooks:
    def __init__(self, workspace, validation_command=None, options=DEFAULTS.harness):
        self.options = options
        self.workspace = workspace
        self.validation_command = validation_command
        self.changed = set()
        self.before_tool = []
        self.after_tool = []
        self.before_finish = []

    def before(self, call, args):
        return [message for hook in self.before_tool if (message := hook(call, args))]

    def after(self, call, args, result):
        if result.ok and call.name in {'write_file', 'apply_proposal'}:
            self.changed.add(args['path'] if call.name == 'write_file' else result.data['path'])
        for hook in self.after_tool:
            hook(call, args, result)

    def validate(self, answer, run_validation):
        errors = [message for hook in self.before_finish if (message := hook(answer))]
        for name in sorted(self.changed):
            path = self.workspace.resolve(name)
            if path.suffix.lower() != '.md':
                continue
            text = path.read_text(encoding='utf-8')
            for target in re.findall(r'\]\(([^\s)]+)', text):
                target = target.strip('<>')
                url = urlsplit(target)
                if url.scheme or not url.path:
                    continue
                if not (path.parent / unquote(url.path)).exists():
                    errors.append(f'{name}: missing local reference {target}')
        if self.changed and self.validation_command:
            result = run_validation(self.validation_command)
            if not result.ok:
                errors.append(str(result.error))
            elif result.data.get('exit_code') != 0:
                errors.append('Validation command failed: ' + result.data.get('output', '')[:self.options.validation_feedback_chars])
        return errors
