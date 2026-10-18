"""Search engine snippets with explicit provenance and a short in-process cache."""
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from ajagent.rag.types import Hit, SearchResult, identifier


def canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {'https', 'http'} or not parts.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith('utm_') and k not in {'gclid', 'fbclid'}]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ''))


class WebSearch:
    def __init__(self, timeout=10, ttl=300, client=None):
        self.timeout, self.ttl = timeout, ttl
        self.client, self.cache = client, {}

    def search(self, query, limit=10):
        if not query.strip() or len(query) > 2000 or not 1 <= limit <= 100:
            raise ValueError('Invalid web search parameters')
        key = (query, limit)
        now = time.monotonic()
        if key in self.cache and now - self.cache[key][0] < self.ttl:
            return self.cache[key][1]
        if self.client is None:
            from ddgs import DDGS
            self.client = DDGS(timeout=self.timeout)
        try:
            rows = self.client.text(query, max_results=limit, backend='duckduckgo')
        except Exception as error:
            # Do not expose URLs containing service tokens in exception messages.
            return SearchResult(warnings=[f'duckduckgo search failed ({type(error).__name__})'])
        result, seen = SearchResult(), set()
        for row in rows:
            uri = canonical_url(row.get('href', ''))
            text = row.get('body', '').strip()
            if not uri or not text or uri in seen:
                continue
            seen.add(uri)
            result.hits.append(Hit(identifier(uri), 'web', uri, text[:2000], row.get('title', ''),
                                   metadata={'engine': 'duckduckgo', 'evidence_type': 'search_snippet'}))
            if len(result.hits) == limit:
                break
        if len(self.cache) >= 128:
            self.cache.pop(next(iter(self.cache)))
        self.cache[key] = (now, result)
        return result
