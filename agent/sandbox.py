"""Read-only database access for the analyst agent: the run_sql tool.

Layers, each one enough on its own for the cases it covers:
1. DuckDB connection: read_only=True, enable_external_access=False, configuration locked. No writes, no file or
   network access, no ATTACH, no extension install.
2. Statement check: exactly one statement, a SELECT starting with SELECT or WITH.
3. Table check (sqlglot, fail closed): only real tables in the scope's schema (marts by default, raw for the raw
   scope), information_schema.tables and information_schema.columns, plus the query's own CTE names. Table functions
   (read_csv, read_text, range, ...) are rejected. The SQL of the models is never readable: information_schema.views,
   duckdb_views(), duckdb_tables() and the sqlite/pg catalog tables get MODEL_SQL_TEXT. In the raw scope every
   reference to information_schema.tables or .columns is rewritten to a subquery filtered to the raw schema, so the
   marts, staging and intermediate tables stay hidden (the marts scope runs the model's SQL verbatim).
4. Limits: 30 seconds per query (the query is interrupted), 50 rows returned.

run_sql never raises: every failure comes back to the model as text.
"""
import datetime as dt
import decimal
import re
import threading
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "warehouse.duckdb"
DEFAULT_SCHEMA = "marts"
ALLOWED_SCHEMAS = ("marts", "information_schema")
SCOPES = {"marts": "marts", "raw": "raw"}  # scope -> the one data schema the agent may read
INFO_SCHEMA_TABLES = ("tables", "columns")  # the only information_schema views that can be read
MODEL_SQL_TEXT = "The SQL of the models is not readable."
MODEL_SQL_SOURCES = frozenset({  # in any schema, as a table or a table function; plus information_schema.views
    "duckdb_views", "duckdb_tables", "sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema",
    "pg_views", "pg_tables", "pg_matviews"})
ALLOWED_CATALOGS = ("", "warehouse")
TIMEOUT_SECONDS = 30
MAX_ROWS = 50
MAX_CELL_CHARS = 300

TOOL = {
    "name": "run_sql",
    "description": (
        "Run one read-only DuckDB SQL query (SELECT or WITH ... SELECT) on the Olist warehouse and get the result "
        "as tab-separated text. Only the marts schema (fct_orders, fct_order_items, fct_order_sellers, "
        "dim_customers, dim_products; unqualified names resolve there) and information_schema can be read. "
        "At most 50 rows are returned, so aggregate or add LIMIT; a query is stopped after 30 seconds. "
        "Errors come back as text starting with ERROR."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"sql": {"type": "string", "description": "One SELECT or WITH query."}},
        "required": ["sql"],
        "additionalProperties": False,
    },
}

RAW_TOOL = {
    **TOOL,
    "description": (
        "Run one read-only DuckDB SQL query (SELECT or WITH ... SELECT) on the Olist warehouse and get the result "
        "as tab-separated text. Only the raw schema (the source tables as loaded; unqualified names resolve there) "
        "and information_schema can be read. "
        "At most 50 rows are returned, so aggregate or add LIMIT; a query is stopped after 30 seconds. "
        "Errors come back as text starting with ERROR."
    ),
}

_ALLOWED_TEXT = "Only tables in the marts schema and information_schema can be read."
_RAW_ALLOWED_TEXT = "Only tables in the raw schema and information_schema can be read."


def tool_for(scope):
    """The run_sql tool definition the model sees in this scope."""
    return {"marts": TOOL, "raw": RAW_TOOL}[scope]
_COMMENTS = re.compile(r"(--[^\n]*)|(/\*.*?\*/)", re.DOTALL)


def connect(db_path=DB_PATH, schema=DEFAULT_SCHEMA):
    """The locked-down connection. Used by Sandbox; tests also use it directly to check layer 1 alone."""
    if schema not in SCOPES.values():
        raise ValueError(f"unknown schema {schema!r}")
    con = duckdb.connect(str(db_path), read_only=True, config={"enable_external_access": False})
    con.execute(f"USE {schema}")
    # Connections to the same file in one process share one DuckDB instance, so a second one finds it locked.
    if not con.execute("SELECT current_setting('lock_configuration')").fetchone()[0]:
        con.execute("SET lock_configuration = true")
    return con


class Sandbox:
    def __init__(self, db_path=DB_PATH, timeout=TIMEOUT_SECONDS, max_rows=MAX_ROWS, scope="marts"):
        if scope not in SCOPES:
            raise ValueError(f"unknown scope {scope!r}")
        self.timeout = timeout
        self.max_rows = max_rows
        self.scope, self.schema = scope, SCOPES[scope]
        self.allowed_schemas = (self.schema, "information_schema")
        self.allowed_text = _ALLOWED_TEXT if scope == "marts" else _RAW_ALLOWED_TEXT
        self.tables_label = "Marts" if scope == "marts" else "Tables"
        self.con = connect(db_path, self.schema)
        self.tables = {r[0].lower() for r in self.con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = ?", [self.schema]).fetchall()}

    def close(self):
        self.con.close()

    # -- checks ------------------------------------------------------------------------------------------

    def check(self, sql):
        """None when the query may run, else the error text for the model."""
        if not sql or not sql.strip():
            return "ERROR: empty query."
        try:
            statements = self.con.extract_statements(sql)
        except duckdb.Error as e:
            return f"ERROR: the query does not parse, so it was rejected: {_one_line(e)} {self.allowed_text}"
        if len(statements) != 1:
            return f"ERROR: send exactly one statement (got {len(statements)})."
        first = _COMMENTS.sub(" ", sql).strip().split(None, 1)[0].upper().lstrip("(")
        if statements[0].type != duckdb.StatementType.SELECT or first not in ("SELECT", "WITH"):
            return "ERROR: only read-only SELECT or WITH ... SELECT queries are accepted."
        return self.check_tables(sql)

    def check_tables(self, sql):
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")
        except Exception as e:  # sqlglot raises several error types; any of them means rejected
            return f"ERROR: the query could not be checked ({type(e).__name__}), so it was rejected. {self.allowed_text}"
        if tree is None:
            return f"ERROR: the query could not be checked, so it was rejected. {self.allowed_text}"
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for table in tree.find_all(exp.Table):
            source, db = (getattr(table.this, "name", "") or "").lower(), table.db.lower()
            if source in MODEL_SQL_SOURCES or (db == "information_schema" and source == "views"):
                return f"ERROR: {MODEL_SQL_TEXT}"
            if db == "information_schema" and source not in INFO_SCHEMA_TABLES:
                return (f"ERROR: information_schema.{source} is not available. Only information_schema.tables and "
                        "information_schema.columns can be read.")
            if not isinstance(table.this, exp.Identifier):
                return f"ERROR: table functions such as {table.sql(dialect='duckdb')[:60]} are not allowed. {self.allowed_text}"
            name, schema, catalog = table.name.lower(), table.db.lower(), table.catalog.lower()
            if catalog not in ALLOWED_CATALOGS:
                return f"ERROR: database {table.catalog} is not available. {self.allowed_text}"
            if not schema:
                if name in cte_names or name in self.tables:
                    continue
                return f"ERROR: unknown table {table.name}. {self.allowed_text} {self.tables_label}: {', '.join(sorted(self.tables))}."
            if schema not in self.allowed_schemas:
                return f"ERROR: schema {table.db} is not available. {self.allowed_text}"
        return None

    # -- execution ---------------------------------------------------------------------------------------

    def prepare(self, sql):
        """(error, None) when the query is rejected, else (None, the SQL to run). The marts scope runs sql as sent;
        the raw scope filters information_schema.tables and .columns to the raw schema."""
        error = self.check(sql)
        if error or self.scope == "marts":
            return error, (None if error else sql)
        tree = sqlglot.parse_one(sql, read="duckdb")
        info = [t for t in tree.find_all(exp.Table)
                if t.db.lower() == "information_schema" and t.name.lower() in INFO_SCHEMA_TABLES]
        if not info:
            return None, sql

        def filtered(node):
            if isinstance(node, exp.Table) and node.db.lower() == "information_schema" \
                    and node.name.lower() in INFO_SCHEMA_TABLES:
                inner = sqlglot.parse_one(f"SELECT * FROM information_schema.{node.name.lower()} "
                                          f"WHERE table_schema = '{self.schema}'", read="duckdb")
                return exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(node.alias_or_name)))
            return node

        rewritten = tree.transform(filtered).sql(dialect="duckdb")
        error = self.check(rewritten)
        return (error, None) if error else (None, rewritten)

    def run_sql(self, sql):
        error, sql = self.prepare(sql)
        if error:
            return error
        timer = threading.Timer(self.timeout, self.con.interrupt)
        timer.start()
        try:
            cur = self.con.execute(sql)
            columns = [d[0] for d in cur.description]
            rows = cur.fetchmany(self.max_rows + 1)
        except duckdb.InterruptException:
            return f"ERROR: query interrupted after {self.timeout:g} seconds. Simplify it or aggregate earlier."
        except duckdb.Error as e:
            return f"ERROR: {type(e).__name__}: {_one_line(e)}"
        except Exception as e:  # never let a query crash the agent
            return f"ERROR: {type(e).__name__}: {_one_line(e)}"
        finally:
            timer.cancel()
        return render(columns, rows, self.max_rows)


def render(columns, rows, max_rows):
    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    lines = ["\t".join(columns)] + ["\t".join(_cell(v) for v in r) for r in rows]
    if not rows:
        lines.append("(0 rows)")
    elif truncated:
        lines.append(f"(truncated: showing the first {max_rows} rows; the query returned more. "
                     "Aggregate or add LIMIT.)")
    else:
        lines.append(f"({len(rows)} row{'s' if len(rows) != 1 else ''})")
    return "\n".join(lines)


def _cell(v):
    if v is None:
        s = "NULL"
    elif isinstance(v, (dt.date, dt.datetime)):
        s = v.isoformat()
    elif isinstance(v, decimal.Decimal):
        s = format(v, "f")
    else:
        s = str(v)
    s = s.replace("\t", " ").replace("\n", " ")
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "..."


def _one_line(e):
    return " ".join(str(e).split())[:500]
