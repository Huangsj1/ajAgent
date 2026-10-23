"""Inspect retrieval without calling a chat model or requiring an API key."""
from ajagent.options import DEFAULT_CONFIG_PATH
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from ajagent.rag.service import RagSettings, build_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['index', 'update', 'search'])
    parser.add_argument('query', nargs='?')
    parser.add_argument('--config', default=DEFAULT_CONFIG_PATH)
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--sources', help='Comma-separated local,semantic,web')
    parser.add_argument('--knowledge', '--source', dest='knowledge', help='Knowledge source directory; may be outside workspace')
    parser.add_argument('--index', '--output', dest='index', help='Index output directory')
    parser.add_argument('--local-path', help='Local search directory or single file')
    parser.add_argument('--rerank', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--mode', choices=['text', 'regex', 'symbol'], default='text')
    parser.add_argument('--glob')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    try:
        sources = args.sources.split(',') if args.sources else None
        settings = RagSettings.load(args.config, sources=sources, rerank=args.rerank)
        for name in ['knowledge', 'index', 'local_path']:
            if getattr(args, name) is not None:
                setattr(settings, name, str(Path(getattr(args, name)).expanduser().absolute()))
        if args.command in {'index', 'update'}:
            from ajagent.rag.knowledge import update_knowledge, open_knowledge
            paths = settings.paths(Path(args.config).expanduser().resolve().parent)
            if args.command == 'update' and args.knowledge is None:
                index = open_knowledge(paths['index'], cache=paths['cache'],
                                       device=settings.device, config_path=args.config, options=settings.options)
                result = index.update()
            else:
                result = update_knowledge(paths['knowledge'], paths['index'], cache=paths['cache'],
                                          device=settings.device, config_path=args.config, options=settings.options)
        else:
            if not args.query:
                parser.error('search requires a query')
            service = build_service(Path(args.workspace), settings, args.config)
            result = asdict(service.search(args.query, limit=args.limit, mode=args.mode, glob=args.glob))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, OSError, ImportError) as error:
        print(f'RAG error ({type(error).__name__}): check paths/configuration and install uv sync --extra local-models')
        return 1
