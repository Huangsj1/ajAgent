"""Compose role adapters without duplicating CLI tool initialization."""
from argparse import Namespace
from dataclasses import replace
from ajagent.config import load_config
from ajagent.models.deepseek import DeepSeekModel
from ajagent.models.openai import OpenAIModel
from ajagent.multi_agent.roles import RoleFactory
from ajagent.multi_agent.coordinator import Coordinator


def build_coordinator(parent, settings, config, options, client, instructions=''):
    def model_factory(role, selected):
        if not selected.get('provider') and not selected.get('model'):
            chosen = settings
        else:
            provider = selected.get('provider', settings.provider)
            model = selected.get('model') or (settings.model if provider == settings.provider else None)
            chosen = load_config(Namespace(config=str(settings.config_path), provider=provider,
                                           model=model, workspace=str(settings.workspace)), config=config)
            chosen = replace(chosen, context_tokens=settings.context_tokens, output_tokens=settings.output_tokens)
        adapter = DeepSeekModel if chosen.provider == 'deepseek' else OpenAIModel
        return adapter(chosen, client)
    factory = RoleFactory(parent, settings, options, model_factory, client)
    factory.prompts = {role: instructions + '\n' + prompt for role, prompt in factory.prompts.items()}
    return Coordinator(parent, factory, options)
