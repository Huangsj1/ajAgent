"""Optional retrieval orchestration: gather, deduplicate, fuse and rerank."""
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from ajagent.rag.types import SearchResult
from ajagent.rag.device import validate_device


@dataclass
class RagSettings:
    enabled: bool = False
    sources: list[str] = field(default_factory=lambda: ['local', 'semantic'])
    knowledge: str = 'examples/knowledge'
    index: str = '.ajagent/rag-index'
    cache: str = '.ajagent/models'
    rerank: bool = True
    device: str = 'auto'
    candidates: int = 10

    @classmethod
    def load(cls, path, enabled=None, sources=None, rerank=None):
        path = Path(path)
        data = tomllib.loads(path.read_text()).get('rag', {}) if path.exists() else {}
        settings = cls(**data)
        for name, value in [('enabled', enabled), ('sources', sources), ('rerank', rerank)]:
            if value is not None:
                setattr(settings, name, value)
        if not isinstance(settings.sources, list) or not settings.sources or set(settings.sources) - {'local', 'semantic', 'web'}:
            raise ValueError('RAG sources must be a nonempty list of local, semantic, web')
        if any(type(getattr(settings, name)) is not bool for name in ('enabled', 'rerank')):
            raise ValueError('enabled and rerank must be booleans')
        if type(settings.candidates) is not int or not 1 <= settings.candidates <= 30:
            raise ValueError('candidates must be 1..30')
        validate_device(settings.device)
        return settings


class RetrievalService:
    def __init__(self, backends, reranker=None, candidates=10):
        self.backends, self.reranker, self.candidates = backends, reranker, candidates

    def search(self, query, sources=None, limit=5, mode='text', glob=None):
        selected = list(dict.fromkeys(sources if sources is not None else self.backends))
        if not query.strip() or len(query) > 2000 or not 1 <= limit <= 20:
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
                result.warnings.append(f'{source} unavailable ({type(error).__name__}); check backend configuration')
                continue
            result.warnings.extend(found.warnings)
            result.truncated |= found.truncated
            # for each search result, merge same records and calculate the score and rank for reciprocal rank fusion (RRF)
            for rank, item in enumerate(found.hits, 1):     # rank start from 1
                key = (item.uri, item.start_line, item.end_line)
                if key not in merged:
                    hit = deepcopy(item)
                    hit.metadata.update(backend_scores={}, rrf=0.0)
                    merged[key] = hit
                hit = merged[key]
                hit.metadata['backend_scores'][source] = item.score
                hit.metadata['rrf'] += 1 / (60 + rank)
        candidates = sorted(merged.values(), key=lambda h: h.metadata['rrf'], reverse=True)[:30]
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


def build_service(root, settings, config_path='config.local.toml'):
    # Construction never downloads models; each adapter loads on first use.
    from ajagent.rag.local.search import LocalSearch
    from ajagent.rag.semantic.encoder import QwenEncoder
    from ajagent.rag.semantic.store import SemanticIndex
    from ajagent.rag.web.search import WebSearch
    from ajagent.rag.ranking.qwen import QwenReranker
    root = Path(root).resolve()
    backends = {}
    if 'local' in settings.sources:
        backends['local'] = LocalSearch(root, config_path)
    if 'semantic' in settings.sources:
        backends['semantic'] = SemanticIndex(root, root / settings.index,
            QwenEncoder(root / settings.cache, settings.device), config_path, settings.knowledge)
    if 'web' in settings.sources:
        backends['web'] = WebSearch()
    reranker = QwenReranker(root / settings.cache, settings.device) if settings.rerank else None
    return RetrievalService(backends, reranker, settings.candidates)
