"""CLI wiring; no agent orchestration lives here."""
import argparse
from pathlib import Path
import httpx
from ajagent.config import load_config
from ajagent.core.loop import run_agent, SYSTEM
from ajagent.models.deepseek import DeepSeekModel
from ajagent.models.openai import OpenAIModel
from ajagent.tools.registry import build_registry


def main():
    parser = argparse.ArgumentParser(description="A small agent with a permission-aware execution harness")
    parser.add_argument("prompt")
    parser.add_argument("--config", default="config.local.toml")
    parser.add_argument("--provider", choices=["deepseek", "openai"])
    parser.add_argument("--model")
    parser.add_argument("--workspace")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--rag", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--rag-sources", help="Comma-separated local,semantic,web")
    parser.add_argument("--rag-rerank", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--rag-index", help="Existing knowledge index directory")
    parser.add_argument("--rag-local-path", help="Local search directory or single file (may be outside workspace)")
    parser.add_argument('--resume', help='Resume a session ID with the same provider/model')
    parser.add_argument('--memory', action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument('--context-tokens', type=int)
    parser.add_argument('--output-tokens', type=int)
    parser.add_argument('--max-tool-calls', type=int)
    parser.add_argument('--max-tokens', type=int)
    parser.add_argument('--max-seconds', type=float)
    parser.add_argument('--deny-tool', action='append', default=[])
    parser.add_argument('--validate-command', help='JSON argv list for checks after writes, e.g. ["pytest","-q"]')
    parser.add_argument('--interactive', action='store_true', help='Accept supplemental input before each step')
    parser.add_argument('--acknowledge-unknown', action='store_true', help='Confirm you inspected the previous unknown side effect')
    args = parser.parse_args()
    try:
        settings = load_config(args)
        registry = build_registry(settings)
        from ajagent.rag.service import RagSettings, build_service
        rag = RagSettings.load(args.config, enabled=args.rag,
                               sources=args.rag_sources.split(',') if args.rag_sources else None,
                               rerank=args.rag_rerank)
        if args.rag_index:
            rag.index = str(Path(args.rag_index).expanduser().resolve())
        if args.rag_local_path:
            rag.local_path = str(Path(args.rag_local_path).expanduser().absolute())
        system_prompt = SYSTEM
        if rag.enabled:
            from ajagent.tools.retrieval.search import retrieval_tool
            tool = retrieval_tool(build_service(settings.workspace, rag, settings.config_path),
                                  settings.max_output_chars)
            registry[tool.name] = tool
            system_prompt += "\nRetrieval is optional. Use it only when helpful. Cite returned URI and lines when using evidence."
        from ajagent.context.builder import ContextBuilder
        from ajagent.memory.session import Session
        from ajagent.memory.long_term import Memory
        instructions = settings.workspace / 'AJAGENT.md'
        if instructions.is_file():
            system_prompt += '\nProject instructions:\n' + instructions.read_text(encoding='utf-8')
        if settings.memory:
            system_prompt += Memory(settings.workspace / '.ajagent').context()
        session = Session(settings.workspace / '.ajagent', settings.provider, settings.model, args.resume)
        builder = ContextBuilder(settings.context_tokens, settings.output_tokens)
        builder.summary = session.checkpoint.get('summary', '')
        builder.covered = session.checkpoint.get('summarized_groups', 0)
        print(f'Session: {session.id}')
        adapter = DeepSeekModel if settings.provider == "deepseek" else OpenAIModel
        print(f"Provider: {settings.provider} | Model: {settings.model}")

        def trace(step, call, result):
            status = "ok" if result.ok else result.error["code"]
            if result.ok and isinstance(result.data, dict) and result.data.get('warnings'):
                status = 'partial' if result.data.get('hits') else 'warning'
            print(f"Step {step} | {call.name} | {status}")
            if call.name == 'retrieve' and not result.ok:
                print(f"  {result.error['message']}")
            if call.name == 'retrieve' and result.ok:
                web = result.data.get('diagnostics', {}).get('web')
                if web:
                    print(f"  web: {web['status']} | attempts={web.get('attempts', 0)}")

        import json
        import sys
        from ajagent.harness.runner import Runner
        from ajagent.harness.budget import Budget
        from ajagent.harness.hooks import Hooks
        from ajagent.tools.execution import Execution
        from ajagent.tools.files.workspace import Workspace
        execution = Execution(Workspace(settings.workspace, settings.config_path, settings.tool_timeout))
        registry.update({tool.name: tool for tool in execution.specs()})
        import tomllib
        config = tomllib.loads(settings.config_path.read_text()) if settings.config_path.exists() else {}
        harness_config = config.get('harness', {})
        validation = json.loads(args.validate_command) if args.validate_command else harness_config.get('validation_command')
        validation = validation or None
        if validation is not None and (not isinstance(validation, list) or not validation or not all(isinstance(x, str) for x in validation)):
            raise ValueError('Validation command must be a JSON argv list')

        def approve(description):
            print(json.dumps(description, ensure_ascii=False, indent=2))
            answer = input('Allow? [once/session/no]: ').strip().lower()
            return answer if answer in {'once', 'session'} else 'deny'

        def supplement(step):
            return input(f'Step {step}: extra instructions (Enter to continue): ').strip()

        # Redact configured credentials from both logs and session persistence.
        import os
        secrets = [getattr(settings, 'api_key', '')]
        secrets += [os.getenv(name, '') for name in ('OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'TAVILY_API_KEY')]
        secrets += [section.get('api_key', '') for section in config.values() if isinstance(section, dict)]
        with httpx.Client() as client:
            runner = Runner(adapter(settings, client), registry, session, builder, execution,
                            ask=approve if sys.stdin.isatty() else None,
                            budget=Budget(args.max_tool_calls if args.max_tool_calls is not None else harness_config.get('max_tool_calls', 30),
                                          args.max_tokens if args.max_tokens is not None else harness_config.get('max_tokens', 100000),
                                          args.max_seconds if args.max_seconds is not None else harness_config.get('max_seconds', 300)),
                            hooks=Hooks(execution.workspace, validation), secrets=secrets,
                            denied=[*harness_config.get('deny_tools', []), *args.deny_tool], supplement=supplement if args.interactive else None,
                            loop=run_agent)
            session.append({'event': 'system', 'content': system_prompt})
            print(f'Run: {runner.events.id}')
            print('[Running Agent]...')
            result = runner.run(args.prompt, max_steps=settings.max_steps,
                                max_output_chars=settings.max_output_chars, on_tool=trace,
                                system_prompt=system_prompt, acknowledge_unknown=args.acknowledge_unknown)
        print(f'Harness: {runner.state.status} | Reason: {runner.state.reason}')
        print(f'Context (estimated): {builder.stats}')
        print(f"[Finished] Status: {result.status}\n{result.answer}\nTotal usage (session): {result.usage}\nLast model request usage: {result.last_usage}")
        return 0 if result.status == "completed" else 1
    except (ValueError, TypeError, OSError, AttributeError):
        print("Status: config_error\nCheck provider, model, credentials, workspace and numeric settings.")
        return 1
    except KeyboardInterrupt:
        print("Status: cancelled")
        return 1
