"""Tree-sitter definitions shared by local symbol search and code chunking."""
from functools import lru_cache
from pathlib import Path

LANGUAGES = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "tsx"}
NODE_TYPES = {"function_definition", "class_definition", "function_declaration", "class_declaration",
              "method_definition", "arrow_function", "generator_function_declaration"}


@lru_cache(maxsize=4)
def language(name):
    from tree_sitter import Language
    if name == "python":
        import tree_sitter_python
        return Language(tree_sitter_python.language())
    if name == "javascript":
        import tree_sitter_javascript
        return Language(tree_sitter_javascript.language())
    import tree_sitter_typescript
    return Language(tree_sitter_typescript.language_tsx() if name == "tsx" else tree_sitter_typescript.language_typescript())


def definitions(path, text):
    """Return named definitions with byte ranges; malformed code falls back upstream."""
    from tree_sitter import Parser
    name = LANGUAGES.get(Path(path).suffix.lower())
    if not name:
        return []
    encoded = text.encode()
    tree = Parser(language(name)).parse(encoded)
    if tree.root_node.has_error:
        raise ValueError("Code contains syntax errors")
    found, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        if node.type in NODE_TYPES:
            named = node.child_by_field_name("name")
            if named is None and node.type == "arrow_function":
                named = node.parent.child_by_field_name("name")
            if named:
                start = node
                if node.parent.type in ("decorated_definition", "export_statement"):
                    start = node.parent
                found.append({"name": encoded[named.start_byte:named.end_byte].decode(),
                              "kind": node.type, "start_byte": start.start_byte, "end_byte": start.end_byte,
                              "start_line": start.start_point.row + 1, "end_line": start.end_point.row + 1})
        stack.extend(reversed(node.children))
    return found
