"""Run model-written SQL safely: read-only, one SELECT, capped rows and a time limit"""

import re
import sqlite3
import time

ROW_CAP = 200
TIME_LIMIT_S = 5.0
# Only reading is allowed; everything else (writes, schema changes, ATTACH, PRAGMA) is denied
ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
if hasattr(sqlite3, "SQLITE_RECURSIVE"):
    ALLOWED_ACTIONS.add(sqlite3.SQLITE_RECURSIVE)
else:
    # Recursive common table expressions use action code 33 in SQLite
    ALLOWED_ACTIONS.add(33)
DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}
COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


class SqlError(Exception):
    """Raised when a query is refused or fails, with a message the model can act on"""


def authorizer(action, arg1, arg2, db_name, trigger):
    """Allow reading tables and calling safe functions; deny every other action"""
    if action not in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def connect_read_only(path):
    """Open the database read-only with query_only on and the authorizer installed"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(authorizer)
    return conn


def check_statement(sql):
    """Refuse empty input and anything that does not start with SELECT or WITH"""
    body = COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not body:
        raise SqlError("The query is empty")
    first = body.split(None, 1)[0].lower()
    if first not in ("select", "with"):
        raise SqlError(f"Only SELECT queries are allowed, not {first.upper()}")


def execute_safe(conn, sql, row_cap=ROW_CAP, time_limit_s=TIME_LIMIT_S):
    """Run one read-only query and return its columns, rows (up to row_cap) and a truncated flag"""
    check_statement(sql)
    deadline = time.monotonic() + time_limit_s
    # Returning a true value from the handler makes SQLite stop the query
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description or []]
        rows = cursor.fetchmany(row_cap + 1)
    except sqlite3.ProgrammingError as error:
        if "one statement" in str(error):
            raise SqlError("Only one SQL statement is allowed per query") from error
        raise SqlError(str(error)) from error
    except sqlite3.DatabaseError as error:
        if "interrupted" in str(error):
            message = f"The query took longer than the time limit of {time_limit_s} s"
            raise SqlError(message) from error
        if "not authorized" in str(error):
            raise SqlError("The query tries to do something other than read data") from error
        raise SqlError(str(error)) from error
    finally:
        conn.set_progress_handler(None, 0)
    truncated = len(rows) > row_cap
    rows = [list(row) for row in rows[:row_cap]]
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}
