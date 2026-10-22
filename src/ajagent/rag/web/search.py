"""Tavily SDK search: one request, evidence results, and explicit errors."""
import math
from ajagent.options import DEFAULTS, TavilySettings
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from requests.exceptions import RequestException, HTTPError
from tavily import TavilyClient
from tavily.errors import (BadRequestError, ForbiddenError, InvalidAPIKeyError,
                           UsageLimitExceededError, TimeoutError as TavilyTimeoutError)
from ajagent.rag.types import Hit, SearchResult, identifier


TAVILY_MAX_RESULTS = 20  # Provider API contract.


def canonical_url(url):
    parts = urlsplit(url)
    if parts.scheme not in {'https', 'http'} or not parts.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.startswith('utm_') and k not in {'gclid', 'fbclid'}]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query), ''))


class WebSearch:
    def __init__(self, settings=None, client=None, options=DEFAULTS.retrieval):
        self.options = options
        self.settings = settings or TavilySettings()
        self.settings.validate()
        self.client = client

    def failure(self, code, message, attempts=1, status_code=None):
        # Error bodies may echo credentials; never forward them unchanged.
        message = str(message)
        if self.settings.api_key:
            message = message.replace(self.settings.api_key, '[REDACTED]')
        message = re.sub(r'tvly-[\w-]+', '[REDACTED]', message)[:self.options.web_error_chars]
        details = {'provider': 'tavily', 'status': 'error', 'error': code, 'attempts': attempts}
        if status_code is not None:
            details['http_status'] = status_code
        return SearchResult(warnings=[f'Tavily {code}: {message}'], diagnostics={'web': details})

    def search(self, query, limit=None):
        limit = self.options.default_results if limit is None else limit
        if not query.strip() or len(query) > self.options.query_chars or not 1 <= limit <= self.options.backend_limit:
            raise ValueError('Invalid web search parameters')
        if not self.settings.api_key.strip():
            return self.failure('missing_api_key', 'Set [tavily].api_key or TAVILY_API_KEY', attempts=0)
        limit = min(limit, TAVILY_MAX_RESULTS)  # Tavily accepts at most 20 results per request.
        payload = {'query': query, 'max_results': limit, 'search_depth': self.settings.search_depth,
                   'topic': 'general', 'auto_parameters': False, 'include_answer': False,
                   'include_raw_content': False, 'include_images': False}
        try:
            if self.client is None:
                self.client = TavilyClient(api_key=self.settings.api_key)
            data = self.client.search(**payload, timeout=self.settings.timeout)
        except TavilyTimeoutError as error:
            return self.failure('timeout', str(error))
        except (BadRequestError, ForbiddenError, InvalidAPIKeyError, UsageLimitExceededError) as error:
            # The SDK exposes typed API errors, without the original HTTP status.
            return self.failure(type(error).__name__, str(error))
        except HTTPError as error:
            status = error.response.status_code if error.response is not None else None
            return self.failure(f'http_{status}', str(error), status_code=status)
        except RequestException as error:
            return self.failure('network_error', str(error))
        except (ValueError, TypeError, AttributeError):
            return self.failure('invalid_response', 'Expected a Tavily JSON response')
        try:
            rows = data['results']
            if not isinstance(rows, list):
                raise ValueError('results must be a list')
            result, seen = SearchResult(), set()
            for row in rows:
                uri = canonical_url(row.get('url', ''))
                content = row.get('content') or ''
                if not uri or not content.strip() or uri in seen:
                    continue
                score = float(row.get('score') or 0)
                if not math.isfinite(score):
                    raise ValueError('Invalid score')
                seen.add(uri)
                result.hits.append(Hit(identifier(uri), 'web', uri, content[:self.settings.max_content_chars], row.get('title') or '',
                    score=score, metadata={'engine': 'tavily', 'evidence_type': 'search_excerpt',
                                           'truncated': len(content) > self.settings.max_content_chars}))
                if len(result.hits) == limit:
                    break
            result.diagnostics['web'] = {'provider': 'tavily', 'status': 'ok' if result.hits else 'empty',
                                         'attempts': 1, 'request_id': data.get('request_id')}
            return result
        except (ValueError, KeyError, TypeError, AttributeError):
            return self.failure('invalid_response', 'Expected Tavily results with valid URL/content/score fields')
