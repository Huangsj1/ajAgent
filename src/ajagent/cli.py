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
    parser = argparse.ArgumentParser(description="A small agent with read-only workspace tools")
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

        with httpx.Client() as client:
            print("[Running Agent]...")
            result = run_agent(args.prompt, adapter(settings, client), registry,
                               settings.max_steps, settings.max_output_chars, trace, system_prompt=system_prompt)
        print(f"[Finished] Status: {result.status}\n{result.answer}\nUsage: {result.usage}")
        return 0 if result.status == "completed" else 1
    except (ValueError, TypeError, OSError, AttributeError):
        print("Status: config_error\nCheck provider, model, credentials, workspace and numeric settings.")
        return 1
    except KeyboardInterrupt:
        print("Status: cancelled")
        return 1
