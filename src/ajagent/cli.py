"""CLI wiring; no agent orchestration lives here."""
from contextlib import ExitStack
import argparse
import json
import os
import sys
from pathlib import Path
import httpx
from ajagent.multi_agent.roles import CoordinationOptions
from ajagent.multi_agent.runtime import build_coordinator
from ajagent.config import load_config
from ajagent.options import DEFAULT_CONFIG_PATH, read_config
from ajagent.core.loop import run_agent, SYSTEM
from ajagent.models.deepseek import DeepSeekModel
from ajagent.models.openai import OpenAIModel
from ajagent.context.builder import ContextBuilder
from ajagent.memory.session import Session
from ajagent.memory.long_term import Memory
from ajagent.harness.runner import Runner
from ajagent.harness.budget import Budget
from ajagent.harness.hooks import Hooks
from ajagent.tools.execution.runtime import Execution
from ajagent.tools.files.workspace import Workspace
from ajagent.tools.runtime.loader import ToolManager
from ajagent.tools.common.base import ToolError


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


def approve(description):
    print(json.dumps(description, ensure_ascii=False, indent=2))
    answer = input('Allow? [once/session/allow_all (all tools in this session)/no]: ').strip().lower()
    return answer if answer in {'once', 'session', 'allow_all'} else 'deny'


def supplement(step):
    return input(f'Step {step}: extra instructions (Enter to continue): ').strip()


def build_parser():
    parser = argparse.ArgumentParser(description="A small agent with a permission-aware execution harness")
    parser.add_argument("prompt")
    parser.add_argument("--task", help="Continue a durable task with fresh context")
    parser.add_argument("--workflow", choices=["single", "research"], help="Override the configured workflow")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--provider", choices=["deepseek", "openai"])
    parser.add_argument("--model")
    parser.add_argument("--workspace")
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--timeout", type=float)
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
    return parser


def main():
    args = build_parser().parse_args()
    try:
        config = read_config(args.config)
        settings = load_config(args, config=config)
        options = settings.options
        coordination = CoordinationOptions.load(config)
        coordinated = args.workflow == "research" or (args.workflow is None and coordination.enabled)
        if coordinated and (args.resume or args.task):
            raise ValueError("Workflow resume is not supported; start a new coordinated task")
        registry = {}
        system_prompt = SYSTEM
        instructions = settings.workspace / 'AJAGENT.md'
        if instructions.is_file():
            system_prompt += '\nProject instructions:\n' + instructions.read_text(encoding='utf-8')
        role_instructions = system_prompt
        if settings.memory:
            system_prompt += Memory(settings.workspace / '.ajagent', options.memory).context()
        session = Session(settings.workspace / '.ajagent', settings.provider, settings.model, args.resume, options.memory)
        if args.task:
            if args.resume and session.task_id != args.task:
                raise ValueError("Task ID conflicts with resumed session")
            session.task_id = args.task
        builder = ContextBuilder(settings.context_tokens, settings.output_tokens, options.context)
        builder.bind(session)
        builder.restore(session.checkpoint, session.groups)
        print(f'Session: {session.id}')
        adapter = DeepSeekModel if settings.provider == "deepseek" else OpenAIModel
        print(f"Provider: {settings.provider} | Model: {settings.model}")

        execution = Execution(Workspace(settings.workspace, settings.config_path, settings.tool_timeout, options.tools))
        harness = options.harness
        validation = json.loads(args.validate_command) if args.validate_command else list(harness.validation_command)
        validation = validation or None
        if validation is not None and (not isinstance(validation, list) or not validation or not all(isinstance(x, str) for x in validation)):
            raise ValueError('Validation command must be a JSON argv list')

        # Redact configured credentials from both logs and session persistence.
        secrets = [getattr(settings, 'api_key', '')]
        secrets += [os.getenv(name, '') for name in ('OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'TAVILY_API_KEY')]
        secrets += [section.get('api_key', '') for section in config.values() if isinstance(section, dict)]
        with ExitStack() as stack:
            client = stack.enter_context(httpx.Client())
            runner = Runner(adapter(settings, client), registry, session, builder, execution,
                            ask=approve if sys.stdin.isatty() else None,
                            budget=Budget(args.max_tool_calls if args.max_tool_calls is not None else harness.max_tool_calls,
                                          args.max_tokens if args.max_tokens is not None else harness.max_tokens,
                                          args.max_seconds if args.max_seconds is not None else harness.max_seconds),
                            hooks=Hooks(execution.workspace, validation, harness), secrets=secrets,
                            denied=args.deny_tool, supplement=supplement if args.interactive else None,
                            loop=run_agent, options=options)
            tool_manager = stack.enter_context(ToolManager(settings, config, runner, client))
            if session.task_id and tool_manager.tasks is None:
                raise ValueError("Enable tasks to continue a durable task")
            system_prompt += tool_manager.instructions
            prompt = tool_manager.prompt(args.prompt)   # expand skills
            print('Tools: ' + ', '.join(runner.registry))
            for warning in tool_manager.warnings:
                print('Tool warning: ' + warning)
            session.append({'event': 'system', 'content': system_prompt})
            print(f'Run: {runner.events.id}')
            print(f'Budget: steps={settings.max_steps}, tools={runner.budget.max_tool_calls}, '
                  f'tokens={runner.budget.max_tokens}, seconds={runner.budget.max_seconds:g}')
            print('[Running Agent]...')
            if coordinated:
                runner.output_limit = settings.max_output_chars
                workflow = build_coordinator(runner, settings, config, coordination, client, role_instructions)
                result = workflow.run(prompt)
                print(f'Workflow artifacts: {workflow.directory}')
            else:
                result = runner.run(prompt, max_steps=settings.max_steps,
                                    max_output_chars=settings.max_output_chars, on_tool=trace,
                                    system_prompt=system_prompt, acknowledge_unknown=args.acknowledge_unknown)
        if tool_manager.tasks and tool_manager.tasks.data:
            print(f"Task: {tool_manager.tasks.data['id']} | Progress: {tool_manager.tasks.directory / 'progress.md'}")
        print(f'Harness: {runner.state.status} | Reason: {runner.state.reason}')
        if runner.budget.phase_reason:
            print(f'Budget phase: {runner.budget.phase} | Trigger: {runner.budget.phase_reason}')
        if coordinated:
            print(f'Workflow nodes: {result.steps}')
        else:
            print(f'Context (estimated): {builder.stats}')
        print(f"[Finished] Status: {result.status}\n{result.answer}\nTotal usage (session): {result.usage}\nLast model request usage: {result.last_usage}")
        return 0 if result.status == "completed" else 1
    except ToolError as error:
        print(f'Status: extension_error\n{error.code}: {error.message}')
        return 1
    except (ValueError, TypeError, OSError, AttributeError):
        print("Status: config_error\nCheck provider, model, credentials, workspace and numeric settings.")
        return 1
    except KeyboardInterrupt:
        print("Status: cancelled")
        return 1
