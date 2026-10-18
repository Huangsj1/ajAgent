from ajagent.rag.web.search import WebSearch, canonical_url


class Client:
    calls = 0

    def text(self, query, **kwargs):
        self.calls += 1
        assert kwargs['backend'] == 'duckduckgo'
        return [{'href': 'https://example.org/a?utm_source=x', 'body': 'Evidence', 'title': 'Page'},
                {'href': 'https://example.org/a', 'body': 'Duplicate'},
                {'href': 'javascript:alert(1)', 'body': 'Bad'}]


def test_dedup_provenance_cache():
    client = Client()
    search = WebSearch(client=client)
    assert len(search.search('q').hits) == 1
    assert search.search('q').hits[0].metadata['evidence_type'] == 'search_snippet'
    assert client.calls == 1
    assert canonical_url('https://Example.org/a?x=1#part') == 'https://example.org/a?x=1'


def test_failure_and_empty():
    class Failed:
        def text(self, *args, **kwargs):
            raise TimeoutError('secret')
    result = WebSearch(client=Failed()).search('q')
    assert not result.hits and 'secret' not in str(result.warnings)
    class Empty:
        def text(self, *args, **kwargs):
            return []
    assert WebSearch(client=Empty()).search('q').hits == []
