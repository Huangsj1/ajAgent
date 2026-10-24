"""Inspect durable tasks and explicitly review user feedback."""
import argparse
import json
from pathlib import Path
from ajagent.options import DEFAULT_CONFIG_PATH, Options, read_config
from ajagent.tasks.feedback import Feedback
from ajagent.tools.common.persistence import identifier


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--config', default=DEFAULT_CONFIG_PATH)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    show = sub.add_parser('show')
    show.add_argument('task_id')
    feedback = sub.add_parser('feedback')
    feedback.add_argument('task_id')
    feedback.add_argument('text')
    for name in ('accept-feedback', 'reject-feedback'):
        review = sub.add_parser(name)
        review.add_argument('task_id')
        review.add_argument('candidate_id')
    args = parser.parse_args()
    try:
        root = Path(args.workspace).resolve() / '.ajagent'
        if args.command == 'list':
            data = [json.loads(path.read_text()) for path in sorted((root / 'tasks').glob('*/tasks.json'))]
            print(json.dumps([{'id':row['id'], 'goal':row['goal'], 'active':row['active']} for row in data], ensure_ascii=False, indent=2))
        elif args.command == 'show':
            print((root / 'tasks' / identifier(args.task_id) / 'progress.md').read_text())
        else:
            store = Feedback(root, args.task_id, Options.from_config(read_config(args.config)).memory)
            value = store.submit(args.text) if args.command == 'feedback' else store.review(args.candidate_id, args.command == 'accept-feedback')
            print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError):
        print('Invalid task, feedback or configuration')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
