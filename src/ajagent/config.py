"""Small, explicit configuration merge with no secret-bearing repr."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from ajagent.options import RUNTIME, MIN_TOOL_OUTPUT_CHARS, Options, read_config


@dataclass
class Settings:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str
    workspace: Path
    config_path: Path
    max_steps: int = RUNTIME.max_steps
    timeout: float = RUNTIME.timeout
    max_output_chars: int = RUNTIME.max_output_chars
    tool_timeout: float = RUNTIME.tool_timeout
    thinking: str = "disabled"
    context_tokens: int = RUNTIME.context_tokens
    output_tokens: int = RUNTIME.output_tokens
    memory: bool = True
    options: Options = field(default_factory=Options, repr=False)


def load_config(args, config=None):
    path = Path(args.config).resolve()
    config = read_config(path) if config is None else config
    runtime = config.get("runtime", {})

    def value(name, default, env=None):
        cli = getattr(args, name, None)
        return cli if cli is not None else os.getenv(env or "AJAGENT_" + name.upper(), runtime.get(name, default))

    provider = value("provider", "deepseek")
    modes = {"deepseek": "chat_completions", "openai": "responses"}
    endpoints = {"deepseek": "https://api.deepseek.com", "openai": "https://api.openai.com/v1"}
    if provider not in modes:
        raise ValueError("Provider must be deepseek or openai")
    section = config.get(provider, {})
    allowed = {"api_key", "model", "base_url", "api_mode", "thinking"}
    if set(section) - allowed:
        raise ValueError("Unsupported provider configuration field")
    if section.get("api_mode", modes[provider]) != modes[provider]:
        raise ValueError("api_mode does not match the selected provider")
    model = getattr(args, "model", None) or os.getenv(provider.upper() + "_MODEL", section.get("model", ""))
    key = os.getenv(provider.upper() + "_API_KEY", section.get("api_key", ""))
    thinking = section.get("thinking", "disabled")
    if thinking not in ("enabled", "disabled") or (provider == "openai" and "thinking" in section):
        raise ValueError("thinking is only supported for DeepSeek")
    settings = Settings(provider, model, key, section.get("base_url", endpoints[provider]),
                        Path(value("workspace", ".")).resolve(), path,
                        int(value("max_steps", RUNTIME.max_steps)), float(value("timeout", RUNTIME.timeout)),
                        int(value("max_output_chars", RUNTIME.max_output_chars)), float(value("tool_timeout", RUNTIME.tool_timeout)), thinking)
    settings.options = Options.from_config(config)
    settings.context_tokens = int(value('context_tokens', RUNTIME.context_tokens))
    settings.output_tokens = int(value('output_tokens', RUNTIME.output_tokens))
    settings.memory = getattr(args, 'memory', None)
    if settings.memory is None:
        settings.memory = runtime.get('memory', True)
    if not isinstance(settings.memory, bool):
        raise ValueError('memory must be boolean')
    if not 1 <= settings.output_tokens < settings.context_tokens:
        raise ValueError('Context budget must exceed output reserve')
    if not isinstance(key, str) or not key.strip() or not isinstance(model, str) or not model.strip():
        raise ValueError("Configure the selected provider's key and model")
    if not settings.workspace.is_dir():
        raise ValueError("Workspace must be an existing directory")
    if settings.max_steps < 1 or settings.max_output_chars < MIN_TOOL_OUTPUT_CHARS:
        raise ValueError(f"max_steps must be positive; max_output_chars must be at least {MIN_TOOL_OUTPUT_CHARS}")
    if any(not math.isfinite(n) or n <= 0 for n in (settings.timeout, settings.tool_timeout)):
        raise ValueError("Timeouts must be finite and positive")
    if not isinstance(settings.base_url, str) or not settings.base_url.startswith("https://"):
        raise ValueError("Use an HTTPS provider endpoint")
    return settings
