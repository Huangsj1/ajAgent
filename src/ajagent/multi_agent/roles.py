"""Role configuration and Runner construction reuse the single-agent harness."""
from copy import copy
from dataclasses import dataclass, field
import json
from ajagent.context.builder import ContextBuilder
from ajagent.harness.budget import ChildBudget
from ajagent.harness.hooks import Hooks
from ajagent.harness.runner import Runner
from ajagent.memory.session import Session
from ajagent.tools.delegation.agent import delegation_tool
from ajagent.tools.common.base import ToolSpec, schema
from ajagent.tools.common.evidence import source_record

ROLES = ('researcher', 'writer', 'reviewer', 'reviser')


@dataclass
class CoordinationOptions:
    enabled: bool = False
    roles_path: str = '../agents'
    max_revisions: int = 2
    node_retries: int = 1
    node_steps: int = 8
    roles: dict = field(default_factory=dict)

    @classmethod
    def load(cls, config):
        options = cls(**config.get('coordination', {}))
        if type(options.enabled) is not bool or not isinstance(options.roles_path, str):
            raise ValueError('Invalid coordination settings')
        for name, upper in (('max_revisions', 2), ('node_retries', 1), ('node_steps', None)):
            value = getattr(options, name)
            if type(value) is not int or value < (1 if name == 'node_steps' else 0) or (upper is not None and value > upper):
                raise ValueError(f'Invalid {name}')
        if not isinstance(options.roles, dict) or options.roles.keys() - set(ROLES):
            raise ValueError('Unknown coordination role')
        for role in options.roles.values():
            if not isinstance(role, dict) or role.keys() - {'provider','model','tools'}:
                raise ValueError('Invalid role configuration')
            if 'provider' in role and role['provider'] not in ('deepseek','openai'):
                raise ValueError('Unsupported role provider')
            if 'model' in role and (not isinstance(role['model'], str) or not role['model']):
                raise ValueError('Invalid role model')
            if 'tools' in role and (not isinstance(role['tools'], list) or any(not isinstance(x,str) for x in role['tools'])):
                raise ValueError('Role tools must be names')
        return options


class RoleFactory:
    def __init__(self, parent, settings, options, model_factory=None, client=None):
        self.parent, self.settings, self.options = parent, settings, options
        self.model_factory, self.client = model_factory, client
        directory = (settings.config_path.parent / options.roles_path).resolve()
        self.prompts = {role: (directory / f'{role}.md').read_text(encoding='utf-8') for role in ROLES}

    def create(self, role):
        parent, settings = self.parent, self.settings
        selected = self.options.roles.get(role, {})
        model = self.model_factory(role, selected) if self.model_factory else copy(parent.model)
        model_settings = getattr(model, 'settings', settings)
        defaults = list(parent.all_options.capabilities.researcher_tools) + ['delegate'] if role == 'researcher' else []
        tools = selected.get('tools', defaults)
        registry = {name: tool for name, tool in parent.registry.items()
                    if name in tools and tool.effect == 'read' and name not in {'delegate','read_artifact'}}
        session = Session(parent.session.path.parent.parent, model_settings.provider, model_settings.model,
                          options=parent.all_options.memory)
        budget = ChildBudget(parent.budget, parent.budget.max_tool_calls, parent.budget.max_tokens,
                             parent.budget.max_seconds)
        child = Runner(model, registry, session,
                       ContextBuilder(model_settings.context_tokens, model_settings.output_tokens, parent.all_options.context),
                       parent.execution, budget=budget, hooks=Hooks(parent.execution.workspace, options=parent.options),
                       denied=parent.policy.denied, options=parent.all_options)
        child.parent = parent
        child.clean = child.session.clean = child.events.clean = parent.clean
        child.hook_lock = parent.hook_lock
        child.hooks.before_tool = list(parent.hooks.before_tool)
        child.hooks.after_tool = list(parent.hooks.after_tool)
        child.observed_evidence = {}

        def capture(call, args, result):
            if result.ok:
                for source in result.sources:
                    record = source_record(parent.clean(source))
                    # Keep bounded delivered tool content, not private child transcripts.
                    observed = json.dumps(parent.clean(result.data), ensure_ascii=False)
                    record['observed'] = observed[:parent.output_limit]
                    record['observed_truncated'] = len(observed) > parent.output_limit
                    child.observed_evidence[record['id']] = record
        child.hooks.after_tool.append(capture)
        if role == 'researcher':
            registry['evidence_catalog'] = ToolSpec('evidence_catalog', 'Get observed evidence IDs for your structured handoff.',
                                              schema({}, []), lambda: ({'evidence': child.evidence.values()}, []))
        if role == 'researcher' and 'delegate' in tools and 'delegate' in parent.registry and 'delegate' not in parent.policy.denied:
            registry['delegate'] = delegation_tool(child, client=self.client, allow_nested=True)
        return child
