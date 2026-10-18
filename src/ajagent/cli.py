"""CLI wiring; no agent orchestration lives here."""
import argparse
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
    args = parser.parse_args()
    try:
        settings = load_config(args)
        registry = build_registry(settings)
        from ajagent.rag.service import RagSettings, build_service
        rag = RagSettings.load(args.config, enabled=args.rag,
                               sources=args.rag_sources.split(',') if args.rag_sources else None,
                               rerank=args.rag_rerank)
        system_prompt = SYSTEM
        if rag.enabled:
            from ajagent.tools.retrieval import retrieval_tool
            tool = retrieval_tool(build_service(settings.workspace, rag, settings.config_path),
                                  settings.max_output_chars)
            registry[tool.name] = tool
            system_prompt += "\nRetrieval is optional. Use it only when helpful. Cite returned URI and lines when using evidence."
        adapter = DeepSeekModel if settings.provider == "deepseek" else OpenAIModel
        print(f"Provider: {settings.provider} | Model: {settings.model}")

        def trace(step, call, result):
            status = "ok" if result.ok else result.error["code"]
            print(f"Step {step} | {call.name} | {status}")

        with httpx.Client() as client:
            result = run_agent(args.prompt, adapter(settings, client), registry,
                               settings.max_steps, settings.max_output_chars, trace, system_prompt=system_prompt)
        print(f"Status: {result.status}\n{result.answer}\nUsage: {result.usage}")
        return 0 if result.status == "completed" else 1
    except (ValueError, TypeError, OSError, AttributeError):
        print("Status: config_error\nCheck provider, model, credentials, workspace and numeric settings.")
        return 1
    except KeyboardInterrupt:
        print("Status: cancelled")
        return 1
