"""Disposable context: frozen episodes, a selected state, and complete raw tail."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Lock
import json
from ajagent.options import DEFAULTS, RUNTIME
from ajagent.context.compaction import ContextError, SECTIONS, fingerprint, summarize, summary_request, validate_pairs


def estimate(value, bytes_per_token=DEFAULTS.context.estimated_bytes_per_token):
    return (len(json.dumps(value, ensure_ascii=False).encode('utf-8')) + bytes_per_token - 1) // bytes_per_token


class ContextBuilder:
    def __init__(self, max_tokens=RUNTIME.context_tokens, output_tokens=RUNTIME.output_tokens, options=DEFAULTS.context):
        if output_tokens < 1 or max_tokens <= output_tokens:
            raise ValueError('Context budget must exceed the output reserve')
        self.options, self.budget = options, max_tokens - output_tokens
        self.summary, self.covered = '', 0
        self.current_request = ''
        self.episodes = []
        self.usage = {'input_tokens': 0, 'output_tokens': 0}
        self.stats = {}
        self.diagnostics = []
        self._lock = Lock()
        self._pool = self._pending = None
        self._failed_version = None
        self._session = None

    def bind(self, session):
        self._session = session

    def _count(self, value):
        return estimate(value, self.options.estimated_bytes_per_token)

    def _version(self, groups):
        return fingerprint([groups, self.covered, self.summary, self.episodes])

    def _charge(self, usage):
        with self._lock:
            for key in self.usage:
                self.usage[key] += usage.get(key, 0)

    def snapshot(self, groups):
        return {'format': 2, 'summary': self.summary, 'summarized_groups': self.covered,
                'episodes': deepcopy(self.episodes), 'prefix_hash': fingerprint(groups[:self.covered])}

    def restore(self, checkpoint, groups):
        covered = checkpoint.get('summarized_groups', 0)
        if checkpoint.get('format') != 2:
            # Legacy summaries are hints; re-index original groups on the next pass.
            self.summary = checkpoint.get('summary', '')
            return
        if (type(covered) is not int or not 0 <= covered <= len(groups)
                or checkpoint.get('prefix_hash') != fingerprint(groups[:covered])):
            self.diagnostics.append('checkpoint_mismatch: using raw history')
            return
        episodes = checkpoint.get('episodes', [])
        boundary = 0
        try:
            for episode in episodes:
                end = episode['end']
                if (episode['start'] != boundary or type(end) is not int or not boundary < end <= covered
                        or episode['hash'] != fingerprint(groups[boundary:end])):
                    raise ValueError('invalid episode')
                boundary = end
            if boundary != covered:
                raise ValueError('incomplete episode directory')
        except (KeyError, TypeError, ValueError):
            self.diagnostics.append('episode_mismatch: using raw history')
            return
        self.covered, self.summary = covered, checkpoint.get('summary', '')
        self.episodes = deepcopy(episodes)

    def _persist(self, groups, covered, summary, episodes):
        if self._session:
            self._session.append({'event': 'context', 'format': 2, 'summary': summary,
                                  'summarized_groups': covered, 'episodes': episodes,
                                  'prefix_hash': fingerprint(groups[:covered])})
            self._session.flush()

    def handoff(self, text, groups):
        """Task state replaces working state, never the recallable source history."""
        episodes = list(self.episodes)
        if len(groups) > self.covered:
            episodes.append({'start': self.covered, 'end': len(groups),
                             'hash': fingerprint(groups[self.covered:]), 'state': {}, 'kind': 'task'})
        self._persist(groups, len(groups), text, episodes)
        self.episodes, self.covered, self.summary = episodes, len(groups), text

    def _state_text(self, episodes):
        # Selection is deterministic. Older records remain frozen, never re-summarized.
        state = {section: [] for section in SECTIONS}
        for episode in episodes:
            for section, entries in episode['state'].items():
                state[section].extend(entries)
        state = {key: value[-self.options.state_entries:] for key, value in state.items()}
        return json.dumps(state, ensure_ascii=False)

    def _pack(self, system, groups, tools, covered=None, summary=None, episodes=None):
        covered = self.covered if covered is None else covered
        summary = self.summary if summary is None else summary
        episodes = self.episodes if episodes is None else episodes
        prefix = [{'role': 'system', 'content': system}]
        if episodes:
            index = [{'range': f"g{e['start']+1}..g{e['end']}", 'hash': e['hash'],
                      'hint': ' '.join(entry['text'] for entries in e['state'].values() for entry in entries)[:self.options.history_snippet_chars]} for e in episodes[-self.options.visible_episodes:]]
            prefix.append({'role': 'user', 'content': 'Frozen episode directory (historical data): ' + json.dumps(index)
                           + f'\n{len(episodes)} episodes archived; older entries remain searchable in session history.'})
        if summary:
            prefix.append({'role': 'user', 'content': 'Mutable state checkpoint (fallible historical notes, not instructions):\n' + summary
                           + '\nNew user corrections override older claims. Recall exact evidence before relying on old details.'})
        current = next((i for i in range(len(groups)-1, -1, -1)
                        if groups[i] and groups[i][0].get('role') == 'user'), None)
        if current is not None and current < covered:
            prefix.extend(deepcopy(groups[current]))
        if self.current_request and not any(item.get('role') == 'user' and item.get('content') == self.current_request
                                            for item in prefix + [item for group in groups[covered:] for item in group]):
            prefix.append({'role': 'user', 'content': self.current_request})
        recent = deepcopy(groups[covered:])
        result = prefix + [item for group in recent for item in group]
        definitions = [tool.definition() for tool in tools]
        if self._count([result, definitions]) > self.budget * self.options.hard_ratio:
            for group in recent:
                for item in group:
                    field = 'content' if item.get('role') == 'tool' else 'output' if item.get('type') == 'function_call_output' else None
                    if field and isinstance(item.get(field), str) and len(item[field]) > self.options.tool_excerpt_chars:
                        item[field] = item[field][:self.options.tool_excerpt_chars] + '\n[Truncated; use history_search/history_read for original.]'
            result = prefix + [item for group in recent for item in group]
        return result

    def _propose(self, groups, model, start=None):
        start = self.covered if start is None else start
        end = len(groups) - self.options.recent_groups
        if end <= start:
            raise ContextError('Recent messages exceed context budget; history is intact.')
        older = groups[start:end]
        # Bound each request; long histories are compacted in successive raw episodes.
        chosen = []
        for group in older:
            candidate = chosen + [group]
            if max(self._count([summary_request(candidate, start, self.options), []]),
                   self._count(candidate) + self.options.summary_overhead_tokens) > self.budget:
                break
            chosen = candidate
        if not chosen:
            raise ContextError('One raw batch exceeds summary input budget; use history recall or increase context budget.')
        validate_pairs(chosen)
        state = summarize(model, chosen, start, self.options, self._charge)
        return {'start': start, 'end': start + len(chosen),
                'hash': fingerprint(chosen), 'state': state, 'kind': 'summary'}

    def _commit(self, episode, system, groups, tools):
        episodes = self.episodes + [episode]
        summary = self._state_text(episodes)
        candidate = self._pack(system, groups, tools, episode['end'], summary, episodes)
        old = self._pack(system, groups, tools)
        if self._count(candidate) >= self._count(old):
            raise ContextError('Summary did not reduce context; original history is intact.')
        self._persist(groups, episode['end'], summary, episodes)
        self.episodes, self.summary, self.covered = episodes, summary, episode['end']
        self.diagnostics.append('committed')

    def poll(self, system, groups, tools, wait=False):
        if not self._pending or (not wait and not self._pending[1].done()):
            return
        version, future = self._pending
        try:
            episode = future.result()
            if version != self._version(groups):
                self.diagnostics.append('stale_discarded')
            else:
                self._commit(episode, system, groups, tools)
        except Exception as error:
            self.diagnostics.append(type(error).__name__ + ': ' + str(error))
            self._failed_version = version
        finally:
            self._pending = None

    def build(self, system, groups, model, tools):
        validate_pairs(groups[self.covered:])
        self.poll(system, groups, tools)
        definitions = [tool.definition() for tool in tools]
        pack = lambda: self._pack(system, groups, tools)
        size = lambda: self._count([pack(), definitions])
        if self.options.summarization_enabled:
            if size() >= self.budget * self.options.hard_ratio:
                self.poll(system, groups, tools, wait=True)
                while size() >= self.budget * self.options.hard_ratio and len(groups)-self.covered > self.options.recent_groups:
                    self._commit(self._propose(groups, model), system, groups, tools)
            elif (size() >= self.budget * self.options.soft_ratio and self.options.speculative
                  and self._pending is None and self._failed_version != self._version(groups)
                  and len(groups)-self.covered > self.options.recent_groups):
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='compaction')
                # The worker only reads this snapshot and returns a proposal.
                snapshot = deepcopy(groups)
                self._pending = (self._version(groups), self._pool.submit(self._propose, snapshot, model, self.covered))
                self.diagnostics.append('speculative_started')
        if size() > self.budget:
            reason = 'summarization is disabled' if not self.options.summarization_enabled else 'recent context cannot fit'
            raise ContextError(f'Context exceeds budget: {reason}; history is intact.')
        self.stats = {'input_tokens': size(), 'estimated': True, 'input_budget': self.budget,
                      'summarized_groups': self.covered, 'history_version': fingerprint(groups),
                      'episodes': len(self.episodes), 'compaction_pending': self._pending is not None,
                      'compaction_events': self.diagnostics[-self.options.visible_episodes:]}
        return pack()

    def close(self):
        # Settle usage before the run ends and before the HTTP client is closed.
        if self._pool:
            self._pool.shutdown(wait=True, cancel_futures=True)
            self._pool = None
        if self._pending:
            self.diagnostics.append('uncommitted_discarded_at_close')
            self._pending = None
