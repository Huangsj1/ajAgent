"""The only tool composition root; registry itself contains no concrete factories."""
from contextlib import ExitStack
from dataclasses import replace
from threading import Semaphore, Lock
from ajagent.tools.runtime.config import ToolConfig
from ajagent.tools.common.base import ToolError


class ToolManager:
    def __init__(self, settings, config, runner, client):
        self.settings, self.config, self.runner, self.client = settings, config, runner, client
        self.selection = ToolConfig.load(config)
        self.stack = ExitStack()
        self.registry = {}
        self.skills = self.mcp = None
        self.instructions = ''
        self.warnings = []

    def __enter__(self):
        try:
            self.load()
        except BaseException:
            self.stack.close()
            raise
        return self

    def __exit__(self, *args):
        self.stack.close()

    def add(self, tools):
        for tool in tools:
            if tool.name in self.selection.disabled:
                continue
            if tool.name in self.registry:
                raise ValueError(f'Duplicate tool name: {tool.name}')
            self.registry[tool.name] = tool

    def load(self):
        settings, runner = self.settings, self.runner
        options = settings.options
        # compose all tools
        factories = {
            'files': lambda: runner.execution.workspace.specs(),
            'database': self.database,
            'execution': lambda: runner.execution.specs(),
            'web': self.web,
            'browser': self.browser,
            'retrieval': self.retrieval,
            'skills': self.skill_tools,
            'delegation': self.delegation,
            'mcp': self.mcp_tools,
            'artifacts': lambda: [runner.artifact_tool()],
        }
        # Load enabled tools in order, and register them with the runner
        for group in self.selection.enabled:
            self.add(factories[group]())
        runner.policy.denied.update(self.selection.disabled)
        runner.registry.clear()
        runner.registry.update(self.registry)
        runner.subagent_slots = Semaphore(options.capabilities.subagent_workers)
        self.instructions += '\nUse available tools only when useful. Cite source URIs/IDs. Search excerpts are not fetched pages.'
        self.instructions += '\nFor independent read tasks, emit multiple tool calls in one step. Writes execute in order.'
        self.instructions += '\nDelegate independent subtasks with explicit contracts; inspect child proposals before applying them.'
        return self.registry

    def database(self):
        from ajagent.tools.database.sqlite import database_tool
        return [database_tool(self.runner.execution.workspace)]

    def web(self):
        from ajagent.tools.web.fetch import fetch_tool
        return [fetch_tool(self.client, self.settings.options.capabilities, self.runner.budget.remaining_seconds)]

    def browser(self):
        from ajagent.tools.web.browser import Browser
        page = Browser(self.settings.options.capabilities, self.runner.budget.remaining_seconds)
        self.stack.callback(page.close)
        if not page.start():
            self.warnings.append(page.unavailable)
        return page.specs()

    def retrieval(self):
        from ajagent.rag.service import RagSettings, build_service
        from ajagent.tools.retrieval.search import retrieval_tool
        settings = RagSettings.load(self.settings.config_path, config=self.config)
        tool = retrieval_tool(build_service(self.settings.workspace, settings, self.settings.config_path), self.settings.max_output_chars)
        lock = Lock()
        def retrieve(**args):
            # Shared model caches/index handles are serialized across child runners too.
            with lock:
                return tool.handler(**args)
        return [replace(tool, handler=retrieve)]

    def skill_tools(self):
        from ajagent.tools.skills.loader import Skills
        self.skills = Skills(self.selection.path(self.selection.skills_path, self.settings.config_path), self.settings.options.capabilities)
        if not self.skills.entries:
            return []
        self.instructions += '\nAvailable skills (metadata only; use load_skill when useful):\n' + self.skills.catalog()
        return [self.skills.spec()]

    def delegation(self):
        from ajagent.tools.delegation.agent import delegation_tool
        from ajagent.tools.delegation.proposals import proposal_tools
        return [delegation_tool(self.runner, client=self.client), *proposal_tools(self.runner)]

    def mcp_tools(self):
        from ajagent.tools.mcp.config import load_servers
        from ajagent.tools.mcp.manager import MCPManager
        configs = load_servers(self.selection.path(self.selection.mcp_config, self.settings.config_path),
                               timeout=self.settings.options.capabilities.mcp_timeout)
        # Add resolved server credentials to all persistence redaction.
        from ajagent.harness.events import redactor
        clean = redactor([value for config in configs for values in (config.headers, config.env) for value in values.values()])
        previous = self.runner.clean
        self.runner.clean = self.runner.session.clean = self.runner.events.clean = lambda value: previous(clean(value))
        self.mcp = MCPManager(configs, self.runner.budget.remaining_seconds)
        self.stack.callback(self.mcp.close)
        self.mcp.connect()
        return self.mcp.specs()

    def prompt(self, prompt):
        if prompt.startswith('/skill ') and self.skills is None:
            raise ToolError('skills_disabled', 'Enable skills in [tools].enabled')
        return self.skills.expand(prompt) if self.skills else prompt
