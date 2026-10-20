"""Bounded UTF-8 file tools with a single workspace boundary."""
import os
from pathlib import Path
import time
from ajagent.tools.base import ToolError, ToolSpec, schema


class Workspace:
    def __init__(self, root, config_path, timeout=5):
        self.root = Path(root).resolve()
        self.config_path = Path(config_path).resolve()
        self.timeout = timeout

    def resolve(self, name):
        path = self.root / name
        # Reject symlinks rather than trying to infer their intended scope.
        if any(p.is_symlink() for p in [path, *path.parents]):
            raise ToolError("forbidden", "Symlinks are not available to file tools")
        path = path.resolve()
        if not path.is_relative_to(self.root):
            raise ToolError("forbidden", "Path must remain inside the workspace")
        parts = path.relative_to(self.root).parts
        if path == self.config_path or any(p.startswith(".") or p == "config.local.toml" for p in parts):
            raise ToolError("forbidden", "Local configuration and hidden paths are excluded")
        if path.is_file() and path.stat().st_nlink > 1:
            raise ToolError("forbidden", "Hard-linked files are excluded")
        return path

    def files(self, directory):
        root = self.resolve(directory)
        if not root.is_dir():
            raise ToolError("not_found", "Directory does not exist")
        deadline = time.monotonic() + self.timeout
        for base, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (Path(base) / d).is_symlink())
            for name in sorted(names):
                if time.monotonic() > deadline:
                    raise ToolError("timeout", "File scan timed out; choose a smaller directory")
                relative = str((Path(base) / name).relative_to(self.root))
                try:
                    path = self.resolve(relative)
                except ToolError:
                    continue
                if path.is_file():
                    yield relative, path

    def read_file(self, path, start_line=1, max_lines=100):
        target = self.resolve(path)
        if not target.is_file():
            raise ToolError("not_found", "File does not exist")
        if target.stat().st_size > 1_000_000:
            raise ToolError("too_large", "File exceeds the 1 MB reading limit")
        lines = target.read_text(encoding="utf-8").splitlines()
        selected = lines[start_line - 1:start_line - 1 + max_lines]
        data = {"lines": [{"line": start_line + i, "text": line} for i, line in enumerate(selected)],
                "truncated": start_line - 1 + max_lines < len(lines)}
        return data, [{"path": path, "start_line": start_line}] if selected else []

    def list_files(self, directory="."):
        entries = []
        for name, _ in self.files(directory):
            entries.append(name)
            if len(entries) == 101:
                break
        return {"files": entries[:100], "truncated": len(entries) > 100}, []

    def search_files(self, query, directory="."):
        matches, skipped, scanned = [], 0, 0
        for name, path in self.files(directory):
            scanned += 1
            if scanned > 1000:
                return {"matches": matches, "truncated": True, "skipped": skipped}, matches
            if path.stat().st_size > 1_000_000:
                skipped += 1
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeError:
                skipped += 1
                continue
            for number, line in enumerate(lines, 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": name, "line": number, "text": line[:500]})
                    if len(matches) >= 50:
                        return {"matches": matches, "truncated": True, "skipped": skipped}, matches
        return {"matches": matches, "truncated": False, "skipped": skipped}, matches

    def specs(self):
        string = {"type": "string", "minLength": 1, "maxLength": 500}
        return [
            ToolSpec("read_file", "Read UTF-8 lines from a workspace file.", schema({
                "path": string, "start_line": {"type": "integer", "minimum": 1},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 200}}, ["path"]), self.read_file),
            ToolSpec("list_files", "List up to 100 workspace files, excluding hidden paths.",
                     schema({"directory": string}, []), self.list_files),
            ToolSpec("search_files", "Search literal text, case-insensitively; returns paths and line numbers.",
                     schema({"query": string, "directory": string}, ["query"]), self.search_files),
        ]
