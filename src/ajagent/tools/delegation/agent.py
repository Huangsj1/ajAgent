"""Parallel isolated workers produce reports/proposals; only the parent writes."""
from copy import copy
import json
from ajagent.tools.delegation.contracts import contract_schema
from ajagent.tools.delegation.proposals import Proposals
from ajagent.context.builder import ContextBuilder
from ajagent.options import RUNTIME
from ajagent.core.loop import SYSTEM
from ajagent.harness.budget import ChildBudget
from ajagent.harness.hooks import Hooks
from ajagent.harness.runner import Runner
from ajagent.memory.session import Session
from ajagent.tools.common.base import ToolError, ToolSpec, schema
from ajagent.tools.execution.runtime import Execution
from ajagent.tools.web.fetch import fetch_tool


def delegation_tool(parent, model_factory=None, client=None, *, allow_nested=False):
    options = parent.all_options
    limits = options.capabilities
    roles = {'researcher': limits.researcher_tools, 'reviewer': limits.reviewer_tools}

    def work(contract, role='researcher'):
        if parent.parent is not None and not allow_nested:
            raise ToolError('delegation_depth', 'Children cannot delegate')
        # Only explicitly selected read tools can cross this boundary.
        registry = {name: tool for name, tool in parent.registry.items()
                    if name in roles[role] and tool.effect == 'read' and name not in {'delegate', 'read_artifact', 'browser_navigate', 'browser_read'}}
        session = Session(parent.session.path.parent.parent, parent.session.provider,
                          parent.session.model, options=options.memory)
        execution = Execution(parent.execution.workspace)
        reserve = getattr(getattr(parent.model, 'settings', None), 'output_tokens', RUNTIME.output_tokens)
        builder = ContextBuilder(parent.builder.budget + reserve, reserve, options.context)
        child = Runner(model_factory() if model_factory else copy(parent.model), registry, session,
                       builder,
                       execution, budget=ChildBudget(parent.budget, limits.child_tool_calls,
                                                    limits.child_tokens, limits.child_seconds),
                       hooks=Hooks(execution.workspace, options=options.harness),
                       denied=parent.policy.denied, options=options)
        # Tool restrictions apply to child calls; parent answer validators do not.
        child.hooks.before_tool = list(parent.hooks.before_tool)
        child.hooks.after_tool = list(parent.hooks.after_tool)
        child.parent = parent
        child.hook_lock = parent.hook_lock
        proposals = Proposals(child, contract)
        if 'propose_patch' not in parent.policy.denied:
            registry['propose_patch'] = proposals.spec()
        # Preserve configured secret redaction in every child persistence path.
        child.clean = child.session.clean = child.events.clean = parent.clean
        if 'fetch_url' in registry and client is not None:
            registry['fetch_url'] = fetch_tool(client, limits, child.budget.remaining_seconds)
        parent.events.emit('delegation_started', parent.step, child_run=child.events.id, role=role)
        result = child.run(json.dumps(contract, ensure_ascii=False), max_steps=limits.child_steps, max_output_chars=parent.output_limit,
                           system_prompt=SYSTEM + f'\nYou are a {role}. Return a source-backed summary and unresolved issues in at most {limits.summary_chars} characters. Keep reports focused; use proposal artifacts for code instead of long prose. Follow the task contract and acceptance criteria. Use propose_patch for drafts or changes, never modify workspace files. Do not delegate.')
        report = {'status': result.status, 'summary': result.answer[:limits.summary_chars],
                  'summary_truncated': len(result.answer) > limits.summary_chars,
                  'proposals': proposals.created, 'evidence': child.evidence.values(), 'usage': dict(child.used), 'run_id': child.events.id}
        # The report is a durable handoff; transcripts stay in the child session.
        artifact = child.events.artifact(dict(report, contract=contract, full_answer=result.answer))
        report['artifacts'] = [str(path) for path in sorted(artifact.parent.glob('*.json'))]
        parent.events.emit('delegation_finished', parent.step, child_run=child.events.id,
                           status=result.status, usage=child.used, artifacts=report['artifacts'])
        if result.status == 'cancelled':
            raise KeyboardInterrupt
        return report, report['evidence']

    def delegate(contract, role='researcher'):
        # All concurrent delegate calls share a bounded admission gate.
        while not parent.subagent_slots.acquire(timeout=parent.all_options.tools.command_poll_seconds):
            parent.budget.check()
        try:
            parent.budget.check()
            return work(contract, role)
        finally:
            parent.subagent_slots.release()

    return ToolSpec('delegate', 'Run an isolated task contract. Emit multiple delegate calls in the SAME step for parallel workers. Children return reports/proposals; parent reviews and applies sequentially.',
                    schema({'contract': contract_schema(limits),
                            'role': {'type': 'string', 'enum': list(roles)}}, ['contract']), delegate)
