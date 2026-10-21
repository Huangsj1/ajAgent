"""Manage explicit memories without a model call or API key."""
import argparse
import json
from pathlib import Path
from ajagent.memory.long_term import Memory


def main():
    parser = argparse.ArgumentParser(description='Explicit workspace memory')
    parser.add_argument('--workspace', default='.')
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('remember')
    add.add_argument('text')
    add.add_argument('--source', default='user')
    add.add_argument('--scope', default='workspace', choices=['workspace'])
    add.add_argument('--id', help='Update an existing memory')
    sub.add_parser('list')
    forget = sub.add_parser('forget')
    forget.add_argument('id')
    args = parser.parse_args()
    try:
        workspace = Path(args.workspace).resolve()
        if not workspace.is_dir():
            raise ValueError('Workspace must exist')
        memory = Memory(workspace / '.ajagent')
        if args.command == 'remember':
            print(memory.remember(args.text, args.source, args.scope, args.id))
        elif args.command == 'list':
            print(json.dumps(memory.list(), ensure_ascii=False, indent=2))
        else:
            memory.forget(args.id)
            print('Forgot memory ' + args.id)
        return 0
    except (ValueError, OSError) as error:
        print(f'Memory error: {error}')
        return 1
