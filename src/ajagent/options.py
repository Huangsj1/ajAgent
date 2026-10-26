"""Shared defaults and tunable limits; no mutable process-global configuration."""
from dataclasses import dataclass, field, fields
import math
from pathlib import Path
import tomllib


@dataclass(frozen=True)
class RuntimeDefaults:
    max_steps: int = 300
    timeout: float = 30
    tool_timeout: float = 5
    max_output_chars: int = 6000
    context_tokens: int = 32000
    output_tokens: int = 4096


@dataclass(frozen=True)
class ToolOptions:
    read_cache_bytes: int = 8388608
    max_file_bytes: int = 1000000
    max_scan_files: int = 1000
    max_list_files: int = 100
    max_matches: int = 50
    excerpt_chars: int = 500
    default_read_lines: int = 100
    max_read_lines: int = 200
    path_chars: int = 500
    write_chars: int = 100000
    diff_chars: int = 3000
    command_output_bytes: int = 12000
    command_chunk_bytes: int = 8192
    command_poll_seconds: float = .1
    command_max_args: int = 40
    command_arg_chars: int = 2000
    allowed_commands: tuple = ('python', 'python3', 'pytest', 'rg')
    sql_rows: int = 50
    sql_max_rows: int = 100
    sql_query_chars: int = 10000
    sql_value_bytes: int = 100000
    sql_progress_steps: int = 1000


@dataclass(frozen=True)
class CapabilityOptions:
    fetch_timeout: float = 15
    fetch_bytes: int = 2000000
    text_chars: int = 12000
    url_chars: int = 2000
    browser_timeout: float = 10
    skill_bytes: int = 32000
    skill_metadata_chars: int = 1000
    child_steps: int = 5
    child_tool_calls: int = 10
    child_tokens: int = 30000
    child_seconds: float = 120
    task_chars: int = 8000
    summary_chars: int = 4000
    mcp_timeout: float = 10
    read_workers: int = 4
    subagent_workers: int = 3
    max_proposals: int = 10
    researcher_tools: tuple = ('read_file', 'list_files', 'search_files', 'query_sqlite', 'retrieve', 'fetch_url')
    reviewer_tools: tuple = ('read_file', 'list_files', 'search_files', 'query_sqlite')


@dataclass(frozen=True)
class ContextOptions:
    summarization_enabled: bool = True
    speculative: bool = True
    soft_ratio: float = 0.80
    hard_ratio: float = 0.95
    state_entries: int = 3
    entry_chars: int = 300
    visible_episodes: int = 3
    history_page_chars: int = 3000
    history_search_results: int = 8
    history_scan_groups: int = 256
    history_snippet_chars: int = 240
    tool_excerpt_chars: int = 1000
    recent_groups: int = 2
    summary_overhead_tokens: int = 256
    summary_output_tokens: int = 2048
    estimated_bytes_per_token: int = 3
    repeated_batches: int = 3


@dataclass(frozen=True)
class MemoryOptions:
    max_entries: int = 20
    text_chars: int = 1000
    source_chars: int = 300
    session_buffer_bytes: int = 65536


@dataclass(frozen=True)
class RetryOptions:
    attempts: int = 3
    backoff_seconds: float = .5


@dataclass(frozen=True)
class HarnessOptions:
    final_step_reserve: int = 1
    repeat_window: int = 12
    low_budget_ratio: float = 0.35
    finalizing_ratio: float = 0.10
    final_input_tokens: int = 4096
    final_output_tokens: int = 1024
    auto_approve_tools: tuple = ()
    max_tool_calls: int = 900
    max_tokens: int = 6000000
    max_seconds: float = 3600
    max_repairs: int = 2
    artifact_threshold_chars: int = 6000
    artifact_preview_chars: int = 1500
    artifact_read_chars: int = 2000
    artifact_max_offset: int = 10000000
    model_log_chars: int = 500
    tool_log_chars: int = 2000
    validation_feedback_chars: int = 2000
    deny_tools: tuple = ()
    validation_command: tuple = ()


@dataclass(frozen=True)
class RetrievalOptions:
    query_chars: int = 2000
    result_limit: int = 20
    backend_limit: int = 100
    default_results: int = 5
    local_timeout: float = 5
    rerank_candidates: int = 30
    rrf_offset: int = 60
    symbol_chars: int = 4000
    rg_batch_files: int = 64
    rg_matches_per_file: int = 5
    chunk_chars: int = 1800
    index_file_bytes: int = 10000000
    index_timeout: float = 60
    hnsw_capacity: int = 16
    hnsw_m: int = 16
    hnsw_construction: int = 200
    hnsw_seed: int = 42
    hnsw_search: int = 64
    hnsw_search_multiplier: int = 4
    embedding_batch: int = 8
    embedding_tokens: int = 1024
    reranker_batch: int = 4
    reranker_tokens: int = 2048
    envelope_chars: int = 300
    web_error_chars: int = 1000
    embedding_dimensions: int = 1024


@dataclass
class TavilySettings:
    api_key: str = field(default='', repr=False)
    timeout: float = 30
    search_depth: str = 'basic'
    max_content_chars: int = 2000

    def validate(self):
        if type(self.max_content_chars) is not int or not 1 <= self.max_content_chars <= 20000:
            raise ValueError('Tavily max_content_chars must be between 1 and 20000')
        if not isinstance(self.api_key, str):
            raise ValueError('Tavily api_key must be a string')
        if isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('Tavily timeout must be positive')
        if self.search_depth not in {'basic', 'advanced', 'fast', 'ultra-fast'}:
            raise ValueError('Unsupported Tavily search_depth')



@dataclass(frozen=True)
class Options:
    tools: ToolOptions = field(default_factory=ToolOptions)
    context: ContextOptions = field(default_factory=ContextOptions)
    memory: MemoryOptions = field(default_factory=MemoryOptions)
    retry: RetryOptions = field(default_factory=RetryOptions)
    harness: HarnessOptions = field(default_factory=HarnessOptions)
    retrieval: RetrievalOptions = field(default_factory=RetrievalOptions)
    capabilities: CapabilityOptions = field(default_factory=CapabilityOptions)

    @classmethod
    def from_config(cls, config):
        groups = {}
        limits = config.get('limits', {})
        known = {item.name for item in fields(cls)} - {'harness'}
        if set(limits) - known:
            raise ValueError('Unknown limits section')
        for item in fields(cls):
            default = item.default_factory()
            values = config.get('harness', {}) if item.name == 'harness' else limits.get(item.name, {})
            valid = {f.name for f in fields(default)}
            if set(values) - valid:
                raise ValueError(f'Unknown {item.name} option')
            annotations = {f.name: f.type for f in fields(default)}
            converted = {}
            for name, value in values.items():
                original = getattr(default, name)
                if isinstance(original, tuple):
                    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
                        raise ValueError(f'{name} must be a list of nonempty strings')
                    value = tuple(value)
                elif annotations[name] is bool:
                    if type(value) is not bool:
                        raise ValueError(f'{name} must be boolean')
                elif annotations[name] is int:
                    minimum = 0 if name in {'max_repairs', 'hnsw_seed', 'read_cache_bytes'} else 1
                    if type(value) is not int or value < minimum:
                        raise ValueError(f'{name} must be an integer >= {minimum}')
                elif annotations[name] is float:
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                        raise ValueError(f'{name} must be positive and finite')
                converted[name] = value
            groups[item.name] = type(default)(**converted)
        result = cls(**groups)
        if result.tools.default_read_lines > result.tools.max_read_lines or result.tools.sql_rows > result.tools.sql_max_rows:
            raise ValueError('Default tool limits exceed maximums')
        if not 0 < result.context.soft_ratio < result.context.hard_ratio < 1:
            raise ValueError('Require 0 < soft_ratio < hard_ratio < 1')
        if not result.tools.allowed_commands:
            raise ValueError('allowed_commands must not be empty')
        if result.retrieval.result_limit > result.retrieval.backend_limit or result.retrieval.default_results > result.retrieval.result_limit:
            raise ValueError('Inconsistent retrieval limits')
        if not 32 <= result.retrieval.embedding_dimensions <= 1024:
            raise ValueError('Embedding dimensions must be between 32 and 1024')
        if result.retrieval.hnsw_m < 2:
            raise ValueError('HNSW M must be at least 2')
        if not 0 < result.harness.finalizing_ratio < result.harness.low_budget_ratio < 1:
            raise ValueError('Require 0 < finalizing_ratio < low_budget_ratio < 1')
        if result.harness.artifact_preview_chars >= result.harness.artifact_threshold_chars:
            raise ValueError('Artifact preview must be smaller than its threshold')
        return result


MIN_TOOL_OUTPUT_CHARS = 1000
DEFAULT_CANDIDATES = 10
RUNTIME = RuntimeDefaults()
DEFAULTS = Options()
DEFAULT_CONFIG_PATH = 'config/config.local.toml'


def read_config(path):
    """Load an entry file and its fragments; entry values override fragments."""
    def merge(target, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    def load(file, parents):
        file = file.resolve()
        if file in parents:
            raise ValueError(f'Circular configuration include: {file.name}')
        data = tomllib.loads(file.read_text(encoding='utf-8'))
        includes = data.pop('include', [])
        if not isinstance(includes, list) or any(not isinstance(item, str) or not item for item in includes):
            raise ValueError('Configuration include must be a list of file paths')
        merged = {}
        for item in includes:
            merge(merged, load(file.parent / item, parents | {file}))
        merge(merged, data)
        return merged

    path = Path(path)
    return load(path, set()) if path.exists() else {}
