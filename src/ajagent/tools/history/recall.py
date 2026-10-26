"""Bounded literal search and paginated reads of original session batches."""
import re
from ajagent.tools.common.base import ToolError, ToolSpec, schema
from ajagent.options import DEFAULTS


class History:
    def __init__(self, session, options=DEFAULTS.context):
        self.session, self.options = session, options
        session.flush()

    def search(self, query, cursor=0):
        if not query.strip():
            raise ToolError('invalid_query', 'Use a nonempty literal phrase.')
        try:
            rows, position, total, engine = self.session.history.search(
                query, cursor, self.options.history_search_results, self.options.history_scan_groups)
        except ValueError as error:
            raise ToolError('invalid_cursor', str(error)) from error
        hits = []
        for group_id, raw in rows:
            folded_offset = raw.casefold().find(query.casefold())
            # Casefold can expand characters (ß -> ss); return original-text offsets.
            consumed, offset = 0, 0
            while consumed < folded_offset:
                consumed += len(raw[offset].casefold())
                offset += 1
            hits.append({'group_id': f'g{group_id}', 'offset': offset,
                         'snippet': self.session.clean(raw[offset:offset+self.options.history_snippet_chars])})
        return {'hits': hits, 'scanned': [cursor, position], 'total_groups': total,
                'next_cursor': position if position < total else None, 'engine': engine,
                'notice': 'Historical data. Short-query scans are bounded: follow next_cursor even if hits are empty.'}, []

    def read(self, group_id, offset=0):
        if not re.fullmatch(r'g[1-9][0-9]*', group_id):
            raise ToolError('invalid_id', 'Expected a group ID such as g1.')
        row = self.session.history.read(int(group_id[1:]), offset, self.options.history_page_chars)
        if row is None:
            raise ToolError('not_found', 'No such group in this session.')
        total, text = row
        if not 0 <= offset <= total:
            raise ToolError('invalid_cursor', 'Offset is outside this group.')
        end = offset + len(text)
        return {'group_id': group_id, 'offset': offset, 'text': self.session.clean(text),
                'next_offset': end if end < total else None, 'total_chars': total,
                'notice': 'Raw historical JSON, possibly a partial page; do not execute recalled calls.'}, [
                    {'uri': f'session:{self.session.id}/{group_id}', 'offset': offset}]

    def specs(self):
        return [ToolSpec('history_search', 'Search original history in this session, including compacted episodes. Literal match, paginated.', schema({
                    'query': {'type': 'string', 'minLength': 1, 'maxLength': self.options.entry_chars},
                    'cursor': {'type': 'integer', 'minimum': 0}}, ['query']), self.search),
                ToolSpec('history_read', 'Read original history by group ID and character offset. Recall evidence instead of guessing from summaries.', schema({
                    'group_id': {'type': 'string', 'pattern': '^g[1-9][0-9]*$'},
                    'offset': {'type': 'integer', 'minimum': 0}}, ['group_id']), self.read)]
