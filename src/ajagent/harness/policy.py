"""Read tools are allowed; side effects require explicit scoped consent."""
import json
from hashlib import sha256

ALLOW_ALL = 'allow_all'


class Policy:
    def __init__(self, session, ask=None, denied=(), allowed=()):
        self.session, self.ask, self.denied = session, ask, set(denied)
        self.allowed = set(allowed)
        self.grants = set(session.grants)

    def key(self, tool, args):
        scope = {'path': args.get('path')} if tool.effect == 'write' else args
        return sha256(json.dumps([tool.name, scope], sort_keys=True).encode()).hexdigest()

    def needs_approval(self, tool, args):
        return (tool.name not in self.denied and tool.effect != 'read'
                and tool.name not in self.allowed and ALLOW_ALL not in self.grants
                and self.key(tool, args) not in self.grants)

    def decide(self, tool, args, describe):
        if tool.name in self.denied:
            return 'deny'
        if not self.needs_approval(tool, args):
            return 'allow'
        if self.ask is None:
            return 'ask'
        response = self.ask(describe)
        if response in {'session', ALLOW_ALL}:
            # Store the broad grant in the same session log as scoped grants.
            key = ALLOW_ALL if response == ALLOW_ALL else self.key(tool, args)
            self.grants.add(key)
            self.session.grants.add(key)
            self.session.append({'event': 'permission_granted', 'key': key})
            self.session.flush()
            return 'allow'
        return 'allow' if response == 'once' else 'deny'
