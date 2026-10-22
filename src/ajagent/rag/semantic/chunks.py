"""Parse once; split prose by headings and code by syntax boundaries."""
import re
from ajagent.options import DEFAULTS
from pathlib import Path
from ajagent.rag.local.syntax import definitions, LANGUAGES

SUPPORTED = {'.md', '.txt', '.rst', '.pdf', *LANGUAGES}


def parse(path):
    """Parse and return pdf context"""
    if path.suffix.lower() == '.pdf':
        from pypdf import PdfReader
        return '\n\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
    return path.read_text(encoding='utf-8')


def chunks(path, text, max_chars=DEFAULTS.retrieval.chunk_chars):
    lines = text.splitlines(keepends=True)
    boundaries = {0, len(lines)}
    names = {}
    section = ''
    # 1. firstly chunk by syntax boundaries for code files, or by markdown headings for text files
    # code file
    if Path(path).suffix in LANGUAGES:
        try:
            # put all symbol boundaries into the set, and map start line to symbol name
            for node in definitions(Path(path), text):
                start, end = node['start_line'] - 1, node['end_line']
                boundaries.update((start, end))
                names[start] = node['name']
        except ValueError:
            pass  # Broken code still has useful text.
    else:   # normal file: use markdown headings or rst section titles as boundaries
        boundaries.update(i for i, line in enumerate(lines) if re.match(r'^#{1,6}\s', line))
    points = sorted(boundaries)
    # 2. chunk original chunks into smaller pieces of max_chars
    for start, end in zip(points, points[1:]):
        # get code / markdown title for the chunk
        if start < len(lines) and re.match(r'^#{1,6}\s', lines[start]):
            section = lines[start].lstrip('#').strip()
        title = names.get(start, section)
        position, buffer = start, ''
        for i in range(start, end):
            if buffer and len(buffer) + len(lines[i]) > max_chars:
                yield position + 1, i, title, buffer
                position, buffer = i, ''
            # Bound even a single minified line.
            for offset in range(0, len(lines[i]), max_chars):
                part = lines[i][offset:offset + max_chars]
                if len(buffer) + len(part) > max_chars:
                    yield position + 1, i + 1, title, buffer
                    position, buffer = i, ''
                buffer += part
        if buffer.strip():
            yield position + 1, end, title, buffer
