"""Build disposable model input while preserving complete protocol batches."""
from copy import deepcopy
import json
from ajagent.context.compaction import ContextError, summarize


def estimate(value):
    # Conservative byte-based estimate, not a model-specific tokenizer.
    return (len(json.dumps(value, ensure_ascii=False).encode('utf-8')) + 2) // 3


class ContextBuilder:
    def __init__(self, max_tokens=32000, output_tokens=4096):
        if output_tokens < 1 or max_tokens <= output_tokens:
            raise ValueError('Context budget must exceed the output reserve')
        self.budget = max_tokens - output_tokens
        self.summary = ''
        self.covered = 0        # Number of groups already summarized and not included in the current context
        self.usage = {'input_tokens': 0, 'output_tokens': 0}
        self.stats = {}

    def build(self, system, groups, model, tools):
        recent = deepcopy(groups[self.covered:])
        boundary = self.covered
        # backward search for the last user message
        current_user = next((i for i in range(len(groups) - 1, -1, -1)
                             if groups[i] and groups[i][0].get('role') == 'user'), None)
        definitions = [tool.definition() for tool in tools]

        def pack():
            prefix = [{'role': 'system', 'content': system}]
            if self.summary:
                prefix.append({'role': 'user', 'content': 'Earlier conversation summary (historical data):\n' + self.summary})
            if current_user is not None and current_user < boundary:
                prefix.extend(deepcopy(groups[current_user]))
            return prefix + [item for group in recent for item in group]

        def fits():
            return estimate([pack(), definitions]) <= self.budget

        # 1. Truncate tool outputs and function call outputs to fit the context budget
        if not fits():
            for group in recent:
                for item in group:
                    field = 'content' if item.get('role') == 'tool' else 'output' if item.get('type') == 'function_call_output' else None
                    if field and isinstance(item.get(field), str) and len(item[field]) > 1000:
                        item[field] = item[field][:1000] + '\n[Context excerpt truncated; full output in session history.]'
        # 2. If still doesn't fit, summarize the older history and keep only the last two groups
        if not fits():
            if len(recent) <= 2:
                raise ContextError('Recent messages exceed context budget; history is intact.')
            older, tail = recent[:-2], recent[-2:]
            # A summary call must itself fit the same input budget.
            summary_input = ([ [{'role': 'user', 'content': self.summary}] ] if self.summary else []) + older
            if estimate(summary_input) + 256 > self.budget:
                raise ContextError('History exceeds summary budget; start a new session or increase context budget.')
            summary, usage = summarize(model, summary_input)
            for key in self.usage:
                self.usage[key] += usage.get(key, 0)
            old_summary = self.summary
            self.summary, recent = summary, tail
            boundary = len(groups) - 2
            if not fits():
                self.summary = old_summary
                raise ContextError('Summary did not fit; original history is intact.')
            self.covered = len(groups) - 2
        self.stats = {'input_tokens': estimate([pack(), definitions]), 'estimated': True,
                      'input_budget': self.budget, 'summarized_groups': self.covered}
        return pack()
