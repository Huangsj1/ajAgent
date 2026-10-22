"""Read tools are allowed; side effects require explicit scoped consent."""
import json
from hashlib import sha256


class Policy:
    def __init__(self, session, ask=None, denied=()):
        self.session, self.ask, self.denied = session, ask, set(denied)
        self.grants = set(session.grants)

    def key(self, tool, args):
        scope = {'path': args.get('path')} if tool.effect == 'write' else args
        return sha256(json.dumps([tool.name, scope], sort_keys=True).encode()).hexdigest()

    def decide(self, tool, args, describe):
        if tool.name in self.denied:
            return 'deny'
        if tool.effect == 'read':
            return 'allow'
        key = self.key(tool, args)
        if key in self.grants:
            return 'allow'
        if self.ask is None:
            return 'ask'
        response = self.ask(describe)
        if response == 'session':
            self.grants.add(key)
            self.session.grants.add(key)
            self.session.append({'event': 'permission_granted', 'key': key})
            self.session.flush()
            return 'allow'
        return 'allow' if response == 'once' else 'deny'
