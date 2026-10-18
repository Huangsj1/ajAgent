"""Bounded ripgrep literal/regex search and Tree-sitter symbol lookup."""
import json
import shutil
import subprocess
import time
from ajagent.rag.local.syntax import definitions, LANGUAGES
from ajagent.rag.types import Hit, SearchResult, identifier
from ajagent.tools.files import Workspace


class LocalSearch:
    def __init__(self, root, config_path="config.local.toml", timeout=5, max_files=1000):
        self.workspace = Workspace(root, config_path, timeout)
        self.timeout, self.max_files = timeout, max_files

    def search(self, query, limit=20, mode="text", glob=None):
        if not query.strip() or len(query) > 2000 or not 1 <= limit <= 100 or mode not in ("text", "regex", "symbol"):
            raise ValueError("Invalid local search parameters")
        files, result = [], SearchResult()
        # 1. Collect all files in the workspace, skipping oversized files and applying glob filtering.
        for name, path in self.workspace.files("."):
            if glob and not path.match(glob):
                continue
            if path.stat().st_size > 1_000_000:
                result.warnings.append(f"Skipped oversized file: {name}")
                continue
            files.append((name, path))
            if len(files) >= self.max_files:
                result.truncated = True
                break
        # 2. Symbol mode: Use Tree-sitter to find named definitions in supported languages.
        if mode == "symbol":
            deadline = time.monotonic() + self.timeout
            for name, path in files:
                if time.monotonic() > deadline:
                    result.warnings.append("AST scan timed out")
                    result.truncated = True
                    break
                if path.suffix not in LANGUAGES:
                    continue
                try:
                    text = path.read_text()
                    encoded = text.encode()
                    # get all symbols: class, function, method, etc. with their byte ranges and line numbers
                    for symbol in definitions(name, text):
                        # substring match on the symbol name
                        if query.casefold() in symbol["name"].casefold():
                            # extract the code excerpt for the symbol and create a Hit object
                            excerpt = encoded[symbol["start_byte"]:symbol["end_byte"]].decode()
                            result.hits.append(Hit(identifier(name, symbol["start_byte"], excerpt), "local", name,
                                excerpt[:4000], symbol["name"], symbol["start_line"], symbol["end_line"],
                                metadata={"kind": symbol["kind"], "truncated": len(excerpt) > 4000}))
                except (UnicodeError, ValueError):
                    result.warnings.append(f"Cannot parse {name}; use text search")
                if len(result.hits) >= limit:
                    result.truncated = True
                    break
        # 3. Text/regex mode: Use ripgrep to search for the query in the collected files
        else:
            if not shutil.which("rg"):
                raise RuntimeError("ripgrep is required for local text search")
            deadline = time.monotonic() + self.timeout
            # handle 64 files at a time
            for offset in range(0, len(files), 64):
                # create the ripgrep command, and use a subprocess to run it with a timeout
                command = ["rg", "--json", "--no-ignore", "--max-count", "5", "--max-columns", "500", "--ignore-case"]
                if mode == "text":
                    command.append("--fixed-strings")
                command.extend(["--", query, *[name for name, _ in files[offset:offset + 64]]])
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result.warnings.append("ripgrep timed out")
                    result.truncated = True
                    break
                try:
                    process = subprocess.run(command, cwd=self.workspace.root, capture_output=True,
                                             text=True, timeout=remaining)
                except subprocess.TimeoutExpired:
                    result.warnings.append("ripgrep timed out")
                    result.truncated = True
                    break
                if process.returncode not in (0, 1):
                    raise ValueError("ripgrep failed; check regex syntax and file access")
                for line in process.stdout.splitlines():
                    event = json.loads(line)
                    if event["type"] != "match":
                        continue
                    data = event["data"]
                    if "text" not in data["path"] or "text" not in data["lines"]:
                        continue
                    # file name, line number, and matched text excerpt(line text)
                    name, number, excerpt = data["path"]["text"], data["line_number"], data["lines"]["text"].rstrip()
                    result.hits.append(Hit(identifier(name, number, excerpt), "local", name, excerpt,
                                           name, number, number, metadata={"kind": mode}))
                if len(result.hits) >= limit:
                    result.truncated = True
                    break
        result.hits = result.hits[:limit]
        return result
