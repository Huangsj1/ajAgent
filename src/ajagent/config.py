"""Small, explicit configuration merge with no secret-bearing repr."""
from dataclasses import dataclass, field
import math
import os
from pathlib import Path
import tomllib


@dataclass
class Settings:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str
    workspace: Path
    config_path: Path
    max_steps: int = 8
    timeout: float = 30
    max_output_chars: int = 6000
    tool_timeout: float = 5
    thinking: str = "disabled"
    context_tokens: int = 32000
    output_tokens: int = 4096
    memory: bool = True


def load_config(args):
    path = Path(args.config).resolve()
    config = tomllib.loads(path.read_text()) if path.exists() else {}
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
                        int(value("max_steps", 8)), float(value("timeout", 30)),
                        int(value("max_output_chars", 6000)), float(value("tool_timeout", 5)), thinking)
    settings.context_tokens = int(value('context_tokens', 32000))
    settings.output_tokens = int(value('output_tokens', 4096))
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
    if settings.max_steps < 1 or settings.max_output_chars < 1000:
        raise ValueError("max_steps must be positive; max_output_chars must be at least 1000")
    if any(not math.isfinite(n) or n <= 0 for n in (settings.timeout, settings.tool_timeout)):
        raise ValueError("Timeouts must be finite and positive")
    if not isinstance(settings.base_url, str) or not settings.base_url.startswith("https://"):
        raise ValueError("Use an HTTPS provider endpoint")
    return settings
