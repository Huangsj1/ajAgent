"""Offline trace inspection and recorded-turn replay; never execute tools."""
import argparse
import json
from pathlib import Path
from ajagent.core.types import ModelTurn, ToolCall


class FakeModel:
    """Scripted turns for harness tests, not deterministic live-model replay."""
    def __init__(self, turns):
        self.turns = iter(turns)

    def generate(self, context, tools):
        turn = dict(next(self.turns))
        turn['tool_calls'] = [ToolCall(**call) for call in turn.get('tool_calls', [])]
        return ModelTurn(**turn)

    def tool_output(self, result, call_id):
        return {'role': 'tool', 'tool_call_id': call_id, 'content': result}


def main():
    parser = argparse.ArgumentParser(description='Read local harness events without executing tools')
    parser.add_argument('run_directory')
    parser.add_argument('--replay', action='store_true', help='Show model/tool summaries in recorded order')
    args = parser.parse_args()
    try:
        rows = [json.loads(line) for line in (Path(args.run_directory) / 'events.jsonl').read_text().splitlines()]
        for row in rows:
            if not args.replay or row['event'] in {'model_finished', 'tool_finished', 'run_finished'}:
                print(json.dumps(row, ensure_ascii=False))
        return 0
    except (ValueError, OSError):
        print('Cannot read run events')
        return 1
