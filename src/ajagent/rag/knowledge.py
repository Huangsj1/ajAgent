"""Public knowledge-base API independent of the agent workspace."""
from pathlib import Path
from ajagent.rag.semantic.encoder import QwenEncoder
from ajagent.rag.semantic.store import SemanticIndex


def update_knowledge(source, output, *, encoder=None, cache='.ajagent/models',
                     device='auto', config_path='config.local.toml'):
    """Create or incrementally update an index; output may be inside source."""
    source, output = Path(source).expanduser().resolve(), Path(output).expanduser().resolve()
    if not source.is_dir():
        raise ValueError('Knowledge source must be an existing directory')
    if source == output or source.is_relative_to(output):
        raise ValueError('Index output must not equal or contain the source directory')
    model = encoder if encoder is not None else QwenEncoder(cache, device)
    return SemanticIndex(source, output, model, config_path).update()


def open_knowledge(output, *, encoder=None, cache='.ajagent/models',
                   device='auto', config_path='config.local.toml'):
    """Load the index location alone; the manifest supplies the source root."""
    model = encoder if encoder is not None else QwenEncoder(cache, device)
    return SemanticIndex.open(output, model, config_path)
