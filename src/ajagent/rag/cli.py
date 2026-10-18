"""Inspect retrieval without calling a chat model or requiring an API key."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from ajagent.rag.service import RagSettings, build_service


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['index', 'search'])
    parser.add_argument('query', nargs='?')
    parser.add_argument('--config', default='config.local.toml')
    parser.add_argument('--workspace', default='.')
    parser.add_argument('--sources', help='Comma-separated local,semantic,web')
    parser.add_argument('--knowledge', help='Knowledge directory relative to workspace')
    parser.add_argument('--index', help='Index directory relative to workspace')
    parser.add_argument('--rerank', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--mode', choices=['text', 'regex', 'symbol'], default='text')
    parser.add_argument('--glob')
    parser.add_argument('--limit', type=int, default=5)
    args = parser.parse_args()
    try:
        sources = args.sources.split(',') if args.sources else None
        settings = RagSettings.load(args.config, sources=sources, rerank=args.rerank)
        for name in ['knowledge', 'index']:
            if getattr(args, name) is not None:
                setattr(settings, name, getattr(args, name))
        if args.command == 'index':
            settings.sources = ['semantic']
        service = build_service(Path(args.workspace), settings, args.config)
        if args.command == 'index':
            result = service.backends['semantic'].update()
        else:
            if not args.query:
                parser.error('search requires a query')
            result = asdict(service.search(args.query, limit=args.limit, mode=args.mode, glob=args.glob))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, OSError, ImportError) as error:
        print(f'RAG error ({type(error).__name__}): check paths/configuration and install uv sync --extra local-models')
        return 1
