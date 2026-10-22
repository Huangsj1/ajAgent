"""Read-only SQLite queries against a workspace database."""
import sqlite3
import math
import time
from ajagent.tools.base import ToolError, ToolSpec, schema


def database_tool(workspace):
    def query_sqlite(path, query, max_rows=None):
        max_rows = workspace.options.sql_rows if max_rows is None else max_rows
        target = workspace.resolve(path)
        if not target.is_file():
            raise ToolError("not_found", "Database file does not exist")
        deadline = time.monotonic() + workspace.timeout
        connection = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True, timeout=workspace.timeout)
        try:
            allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
            connection.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY)
            connection.set_progress_handler(lambda: int(time.monotonic() > deadline), workspace.options.sql_progress_steps)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, workspace.options.sql_value_bytes)
            cursor = connection.execute(query)
            rows = cursor.fetchmany(max_rows + 1)
            # Blobs are represented as hex so every result remains JSON serializable.
            def json_value(value):
                if isinstance(value, bytes):
                    return value.hex()
                if isinstance(value, float) and not math.isfinite(value):
                    return str(value)
                return value

            values = [[json_value(v) for v in row] for row in rows[:max_rows]]
            data = {"columns": [c[0] for c in cursor.description], "rows": values,
                    "truncated": len(rows) > max_rows}
            return data, [{"path": path, "query": query}] if values else []
        except sqlite3.Error as error:
            if time.monotonic() >= deadline or "locked" in str(error):
                raise ToolError("timeout", "SQLite query timed out; simplify the query") from None
            raise ToolError("sql_error", "Query failed; use a single read-only SELECT and check table names") from None
        finally:
            connection.close()

    return ToolSpec("query_sqlite", "Run one read-only SELECT in a workspace SQLite database.", schema({
        "path": {"type": "string", "minLength": 1},
        "query": {"type": "string", "minLength": 1, "maxLength": workspace.options.sql_query_chars},
        "max_rows": {"type": "integer", "minimum": 1, "maximum": workspace.options.sql_max_rows}}, ["path", "query"]), query_sqlite)
