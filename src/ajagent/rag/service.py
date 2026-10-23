"""Optional retrieval orchestration: gather, deduplicate, fuse and rerank."""
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from ajagent.options import DEFAULT_CONFIG_PATH, DEFAULTS, DEFAULT_CANDIDATES, Options, read_config
import os
from ajagent.options import TavilySettings
from ajagent.rag.types import SearchResult, identifier
from ajagent.rag.device import validate_device


@dataclass
class RagSettings:
    sources: list[str] = field(default_factory=lambda: ['local', 'semantic'])
    knowledge: str = 'knowledge'
    local_path: str = ''
    index: str = 'knowledge/.index'
    cache: str = '.ajagent/models'
    rerank: bool = True
    device: str = 'auto'
    candidates: int = DEFAULT_CANDIDATES
    options: Options = field(default_factory=Options, repr=False)
    tavily: TavilySettings = field(default_factory=TavilySettings, repr=False)

    def paths(self, base):
        """TOML paths are relative to its directory; CLI overrides are absolute."""
        return {name: (Path(base) / Path(getattr(self, name)).expanduser()).resolve()
                for name in ['knowledge', 'index', 'cache', 'local_path']}

    @classmethod
    def load(cls, path, sources=None, rerank=None, config=None):
        path = Path(path)
        config = read_config(path) if config is None else config
        tavily = dict(config.get('tavily', {}))
        if 'TAVILY_API_KEY' in os.environ:
            tavily['api_key'] = os.environ['TAVILY_API_KEY']
        options = Options.from_config(config)
        values = dict(config.get('rag', {}))
        settings = cls(**values, tavily=TavilySettings(**tavily), options=options)
        for name, value in [('sources', sources), ('rerank', rerank)]:
            if value is not None:
                setattr(settings, name, value)
        if not isinstance(settings.sources, list) or not settings.sources or set(settings.sources) - {'local', 'semantic', 'web'}:
            raise ValueError('RAG sources must be a nonempty list of local, semantic, web')
        if any(type(getattr(settings, name)) is not bool for name in ('rerank',)):
            raise ValueError('rerank must be boolean')
        if type(settings.candidates) is not int or not 1 <= settings.candidates <= options.retrieval.rerank_candidates:
            raise ValueError(f'candidates must be 1..{options.retrieval.rerank_candidates}')
        validate_device(settings.device)
        settings.tavily.validate()
        return settings


class RetrievalService:
    def __init__(self, backends, reranker=None, candidates=DEFAULT_CANDIDATES, options=DEFAULTS.retrieval):
        self.options = options
        self.backends, self.reranker, self.candidates = backends, reranker, candidates

    def search(self, query, sources=None, limit=None, mode='text', glob=None):
        limit = self.options.default_results if limit is None else limit
        selected = list(dict.fromkeys(sources if sources is not None else self.backends))
        if not query.strip() or len(query) > self.options.query_chars or not 1 <= limit <= self.options.result_limit:
            raise ValueError('Invalid retrieval query or limit')
        if not selected or set(selected) - self.backends.keys():
            raise ValueError('Requested source is not enabled')
        result, merged = SearchResult(), {}
        for source in selected:
            try:
                # local search type & filter
                kwargs = {'mode': mode, 'glob': glob} if source == 'local' else {}
                # Search each backend
                found = self.backends[source].search(query, max(limit, self.candidates), **kwargs)
            except Exception as error:
                result.diagnostics[source] = {'status': 'error', 'error': type(error).__name__}
                result.warnings.append(f'{source} unavailable ({type(error).__name__}); check backend configuration')
                continue
            result.diagnostics.update(found.diagnostics)
            result.warnings.extend(found.warnings)
            result.truncated |= found.truncated
            # for each search result, merge same records and calculate the score and rank for reciprocal rank fusion (RRF)
            for rank, item in enumerate(found.hits, 1):     # rank start from 1
                item = deepcopy(item)
                backend = self.backends[source]
                if source in {'local', 'semantic'} and hasattr(backend, 'workspace'):
                    item.metadata['relative_path'] = item.uri
                    item.uri = str(backend.workspace.root / item.uri)
                    item.id = identifier(item.uri, item.id)
                key = (item.uri, item.start_line, item.end_line)
                if key not in merged:
                    hit = deepcopy(item)
                    hit.metadata.update(backend_scores={}, rrf=0.0)
                    merged[key] = hit
                hit = merged[key]
                hit.metadata['backend_scores'][source] = item.score
                hit.metadata['rrf'] += 1 / (self.options.rrf_offset + rank)
        candidates = sorted(merged.values(), key=lambda h: h.metadata['rrf'], reverse=True)[:self.options.rerank_candidates]
        for hit in candidates:
            hit.score = hit.metadata['rrf']
        if self.reranker and candidates:
            try:
                # use Qwen reranker to score the candidates and sort them by the reranker score
                scores = self.reranker.score(query, [h.text for h in candidates])
                if len(scores) != len(candidates):
                    raise ValueError('Reranker result count differs')
                for hit, score in zip(candidates, scores):
                    # Update the hit score with the reranker score and store it in metadata for reference
                    hit.score = score
                    hit.metadata['rerank_score'] = score
                candidates.sort(key=lambda h: h.score, reverse=True)
            except Exception as error:
                result.warnings.append(f'Rerank unavailable ({type(error).__name__}); using reciprocal rank fusion')
        result.hits = candidates[:limit]
        result.truncated |= len(candidates) > limit
        return result


def build_service(root, settings, config_path=DEFAULT_CONFIG_PATH):
    # Construction never downloads models; each adapter loads on first use.
    from ajagent.rag.local.search import LocalSearch
    from ajagent.rag.semantic.encoder import QwenEncoder
    from ajagent.rag.semantic.store import SemanticIndex
    from ajagent.rag.web.search import WebSearch
    from ajagent.rag.ranking.qwen import QwenReranker
    root = Path(root).expanduser().resolve()
    base = Path(config_path).expanduser().resolve().parent
    paths = settings.paths(base)
    backends = {}
    if 'local' in settings.sources:
        backends['local'] = LocalSearch(paths['local_path'] if settings.local_path else root, config_path, options=settings.options)
    if 'semantic' in settings.sources:
        encoder = QwenEncoder(paths['cache'], settings.device, options=settings.options.retrieval)
        if (paths['index'] / 'CURRENT').exists():
            backends['semantic'] = SemanticIndex.open(paths['index'], encoder, config_path, options=settings.options)
        else:
            backends['semantic'] = SemanticIndex(paths['knowledge'], paths['index'], encoder, config_path, options=settings.options)
    if 'web' in settings.sources:
        backends['web'] = WebSearch(settings.tavily, options=settings.options.retrieval)
    reranker = QwenReranker(paths['cache'], settings.device, options=settings.options.retrieval) if settings.rerank else None
    return RetrievalService(backends, reranker, settings.candidates, settings.options.retrieval)
