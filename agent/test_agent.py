"""Tests for the analyst agent: sandbox, model loop (fake client, no API calls), prompt and isolation.

Run from the repo root:  python agent/test_agent.py   (or pytest agent/test_agent.py)
Needs warehouse.duckdb with no writer holding the lock.
"""
import io
import json
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import duckdb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyst  # noqa: E402
import sandbox  # noqa: E402

ROOT = HERE.parent
EVAL_FILE = "/".join(["evals", "questions" + ".yaml"])  # built from parts: the literal must not appear in agent/
_SB = None


def sb():
    global _SB
    if _SB is None:
        _SB = sandbox.Sandbox(timeout=1)
    return _SB


def is_error(out):
    return out.startswith("ERROR")


def assert_rejected(sql, label, contains=None):
    out = sb().run_sql(sql)
    assert is_error(out), f"{label}: not rejected: {out[:200]}"
    if contains:
        assert contains in out, f"{label}: {out[:300]}"
    return out


# -- sandbox -----------------------------------------------------------------------------------------------

def test_reading_files_fails():
    assert_rejected(f"SELECT * FROM read_text('{EVAL_FILE}')", "read_text")
    assert_rejected("SELECT * FROM read_csv('data/raw/olist_orders_dataset.csv')", "read_csv")
    assert_rejected(f"SELECT content FROM read_text('{ROOT / EVAL_FILE}') LIMIT 1", "read_text absolute")


def test_writing_fails():
    for sql in ("CREATE TABLE marts.t AS SELECT 1 AS a",
                "INSERT INTO fct_orders SELECT * FROM fct_orders LIMIT 1",
                "COPY (SELECT * FROM fct_orders) TO 'out.csv'",
                "WITH x AS (SELECT 1) INSERT INTO fct_orders SELECT * FROM fct_orders",
                "ATTACH 'other.duckdb' AS other",
                "SET enable_external_access = true",
                "SELECT 1; DROP TABLE fct_orders"):
        assert_rejected(sql, sql)
    assert not (ROOT / "out.csv").exists() and not (ROOT / "other.duckdb").exists()


def test_connection_alone_blocks_files_and_writes():
    """Layer 1 without the statement and table checks: DuckDB itself refuses."""
    con = sandbox.connect()
    try:
        for sql in (f"SELECT * FROM read_text('{EVAL_FILE}')",
                    "SELECT * FROM read_csv('data/raw/olist_orders_dataset.csv')",
                    "CREATE TABLE t (a INTEGER)",
                    "INSERT INTO fct_orders SELECT * FROM fct_orders LIMIT 1",
                    "COPY (SELECT 1) TO 'out.csv'",
                    "ATTACH 'other.duckdb' AS other",
                    "SET enable_external_access = true"):
            try:
                con.execute(sql).fetchall()
            except duckdb.Error:
                continue
            raise AssertionError(f"connection accepted: {sql}")
    finally:
        con.close()
    assert not (ROOT / "out.csv").exists() and not (ROOT / "other.duckdb").exists()


def test_timeout_interrupts_query():
    out = assert_rejected("SELECT sum(hash(a.order_item_key || b.order_item_key)) AS h "
                          "FROM fct_order_items a, fct_order_items b", "timeout", contains="interrupted")
    assert sb().run_sql("SELECT count(*) AS n FROM fct_orders").startswith("n\n"), "connection unusable after timeout"
    return out


def test_marts_only():
    assert_rejected("SELECT * FROM raw.orders", "raw.orders", contains="marts")
    assert_rejected('SELECT * FROM "RAW"."Orders"', "quoted raw", contains="marts")
    assert_rejected('SELECT * FROM Raw.ORDERS', "mixed case raw", contains="marts")
    assert_rejected("SELECT * FROM warehouse.raw.orders", "catalog-qualified raw", contains="marts")
    assert_rejected("SELECT * FROM staging.stg_orders", "staging", contains="marts")
    assert_rejected("SELECT * FROM intermediate.int_orders_enriched", "intermediate", contains="marts")
    assert_rejected("SELECT * FROM fct_orders o JOIN raw.customers c USING (customer_id)", "join to raw")
    assert_rejected("SELECT * FROM (SELECT * FROM raw.orders) t", "raw in subquery")
    assert_rejected("WITH x AS (SELECT * FROM raw.orders) SELECT * FROM x", "raw in CTE")
    assert_rejected("SELECT * FROM range(10)", "table function", contains="marts")
    assert_rejected("SELECT * FROM duckdb_tables()", "catalog function")
    assert_rejected("SELECT * FROM pg_tables", "unknown unqualified table")


def test_unparseable_and_non_select_rejected():
    assert_rejected("SELEC * FRM fct_orders", "typo", contains="marts")
    assert_rejected("", "empty")
    assert_rejected("USE raw", "use")
    assert_rejected("FROM fct_orders", "from-first")
    assert_rejected("DESCRIBE fct_orders", "describe")


def test_allowed_queries():
    ok = {
        "unqualified": "SELECT count(*) AS n FROM fct_orders",
        "qualified": "SELECT count(*) AS n FROM marts.fct_orders",
        "quoted": 'SELECT count(*) AS n FROM "MARTS"."FCT_ORDERS"',
        "cte": "WITH d AS (SELECT * FROM fct_orders WHERE is_delivered) SELECT count(*) AS n FROM d",
        "cte shadowing": "WITH fct_orders AS (SELECT 1 AS a) SELECT count(*) AS n FROM fct_orders",
        "subquery alias": "SELECT count(*) AS n FROM (SELECT order_id FROM fct_order_items) AS t",
        "join": "SELECT count(*) AS n FROM fct_orders o LEFT JOIN dim_customers c USING (customer_unique_id)",
        "information_schema": "SELECT column_name AS n FROM information_schema.columns "
                              "WHERE table_name = 'fct_orders' LIMIT 3",
        "comment first": "-- revenue\nSELECT sum(order_price) AS n FROM fct_orders WHERE is_delivered",
    }
    for label, sql in ok.items():
        out = sb().run_sql(sql)
        assert not is_error(out) and out.startswith("n\n"), f"{label}: {out[:300]}"


def test_rows_capped_at_50():
    out = sb().run_sql("SELECT order_id FROM fct_orders LIMIT 120")
    lines = out.splitlines()
    assert lines[0] == "order_id" and len(lines) == 1 + 50 + 1, len(lines)
    assert "truncated" in lines[-1] and "50" in lines[-1]
    out = sb().run_sql("SELECT order_id FROM fct_orders LIMIT 50")
    assert "truncated" not in out and out.splitlines()[-1] == "(50 rows)"


def test_errors_returned_as_text():
    out = sb().run_sql("SELECT no_such_column FROM fct_orders")
    assert out.startswith("ERROR: BinderException"), out[:200]


# -- loop with a fake client ------------------------------------------------------------------------------

def usage(i=100, o=10, cr=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=0)


def text_block(t):
    return SimpleNamespace(type="text", text=t)


def tool_block(n, sql):
    return SimpleNamespace(type="tool_use", id=f"tu_{n}", name="run_sql", input={"sql": sql})


ANSWER = 'There were 1 orders.\n\n```json\n{"status": "answered", "assumptions": [], "answer": {"n": 1}}\n```'


class FakeClient:
    def __init__(self, script):
        self.script = script  # function(call_index, kwargs) -> response, or raises
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append({"tool_choice": kwargs["tool_choice"], "n_messages": len(kwargs["messages"]),
                           "last": kwargs["messages"][-1], "model": kwargs["model"], "system": kwargs["system"],
                           "first": kwargs["messages"][0]})
        return self.script(len(self.calls) - 1, kwargs)


PAYLOAD = {"question": "How many orders?", "answer_schema": {"status": "answered | cannot_answer",
                                                               "answer": {"n": "integer"}}}


def run_main(client, env=None, payload=PAYLOAD, sleeps=None):
    out, err = io.StringIO(), io.StringIO()
    code = analyst.main(stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=err,
                        env={analyst.KEY_ENV: "sk-test-secret-123"} if env is None else env,
                        client_factory=lambda key: client, sandbox_factory=lambda: sandbox.Sandbox(timeout=5),
                        sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    return code, out.getvalue(), err.getvalue()


def usage_line(err):
    lines = [ln for ln in err.splitlines() if ln.startswith("USAGE ")]
    assert len(lines) == 1 and err.rstrip().endswith(lines[0]), f"USAGE line: {err!r}"
    return json.loads(lines[0][len("USAGE "):])


def test_one_tool_call_then_answer():
    def script(i, kw):
        if i == 0:
            return SimpleNamespace(stop_reason="tool_use", usage=usage(cr=50),
                                   content=[text_block("Counting."), tool_block(1, "SELECT count(*) AS n FROM fct_orders")])
        result = kw["messages"][-1]["content"][0]
        assert result["type"] == "tool_result" and result["tool_use_id"] == "tu_1"
        assert result["content"].startswith("n\n99441") and not result["is_error"], result
        return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])
    client = FakeClient(script)
    code, out, err = run_main(client)
    assert code == 0, err
    assert out.strip() == ANSWER
    assert usage_line(err) == {"input_tokens": 250, "output_tokens": 20, "tool_calls": 1}
    assert [c["model"] for c in client.calls] == ["claude-sonnet-5-5"] * 2
    assert all(c["tool_choice"] == {"type": "auto"} for c in client.calls)


def test_budget_exhausted():
    def script(i, kw):
        if kw["tool_choice"] == {"type": "none"}:
            return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])
        # two queries per turn, so the last batch crosses the budget
        return SimpleNamespace(stop_reason="tool_use", usage=usage(),
                               content=[tool_block(2 * i, "SELECT 1 AS n FROM fct_orders LIMIT 1"),
                                        tool_block(2 * i + 1, "SELECT 2 AS n FROM fct_orders LIMIT 1")])
    client = FakeClient(script)
    code, out, err = run_main(client)
    assert code == 0, err
    assert out.strip() == ANSWER
    assert usage_line(err)["tool_calls"] == analyst.MAX_TOOL_CALLS
    choices = [c["tool_choice"]["type"] for c in client.calls]
    assert choices == ["auto"] * 5 + ["none"], choices
    last = client.calls[-1]["last"]["content"]
    assert last[-1] == {"type": "text", "text": analyst.BUDGET_MESSAGE}
    assert sum(b.get("type") == "tool_result" for b in last) == 2


def test_budget_exhausted_mid_batch():
    def script(i, kw):
        if kw["tool_choice"] == {"type": "none"}:
            return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])
        return SimpleNamespace(stop_reason="tool_use", usage=usage(),
                               content=[tool_block(f"{i}_{k}", "SELECT 1 AS n") for k in range(3)])
    client = FakeClient(script)
    code, _, err = run_main(client)
    assert code == 0 and usage_line(err)["tool_calls"] == 10
    last = client.calls[-1]["last"]["content"]
    results = [b for b in last if b.get("type") == "tool_result"]
    assert [r["content"] == analyst.OVER_BUDGET_RESULT for r in results] == [False, True, True]


def test_api_error_exits_nonzero():
    import anthropic
    import httpx2

    def server_error(i, kw):
        raise anthropic.InternalServerError(
            "boom", response=httpx2.Response(500, request=httpx2.Request("POST", "https://api.invalid")), body=None)
    sleeps = []
    client = FakeClient(server_error)
    code, out, err = run_main(client, sleeps=sleeps)
    assert code == 1 and out == ""
    assert len(client.calls) == 1 + analyst.MAX_RETRIES and len(sleeps) == analyst.MAX_RETRIES
    assert sleeps == sorted(sleeps), sleeps  # backoff grows
    assert "InternalServerError" in err and "status 500" in err
    assert usage_line(err) == {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0}

    def rate_limited_once(i, kw):
        if i == 0:
            raise anthropic.RateLimitError(
                "slow down", response=httpx2.Response(429, request=httpx2.Request("POST", "https://api.invalid")),
                body=None)
        return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])
    code, out, _ = run_main(FakeClient(rate_limited_once))
    assert code == 0 and out.strip() == ANSWER

    def bad_request(i, kw):
        raise anthropic.BadRequestError(
            "bad", response=httpx2.Response(400, request=httpx2.Request("POST", "https://api.invalid")), body=None)
    client = FakeClient(bad_request)
    code, _, err = run_main(client)
    assert code == 1 and len(client.calls) == 1, "4xx must not be retried"


def test_refusal_and_truncation_exit_nonzero():
    for stop in ("refusal", "max_tokens"):
        client = FakeClient(lambda i, kw: SimpleNamespace(stop_reason=stop, usage=usage(), content=[text_block("x")]))
        code, out, err = run_main(client)
        assert code == 1 and out == "" and stop in err, (stop, err)
        assert usage_line(err)["output_tokens"] == 10


def test_bad_input_and_missing_key():
    code, out, err = run_main(FakeClient(None), env={})
    assert code == 2 and analyst.KEY_ENV in err and out == ""
    usage_line(err)
    out_, err_ = io.StringIO(), io.StringIO()
    code = analyst.main(stdin=io.StringIO('{"question": "x"}'), stdout=out_, stderr=err_,
                        env={analyst.KEY_ENV: "k"}, client_factory=lambda k: None)
    assert code == 2 and "bad input" in err_.getvalue()


def test_key_never_printed():
    secret = "sk-test-secret-123"
    for script in (lambda i, kw: SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)]),
                   lambda i, kw: (_ for _ in ()).throw(RuntimeError(secret))):
        _, out, err = run_main(FakeClient(script))
        assert secret not in out + err


# -- prompt and isolation ---------------------------------------------------------------------------------

def test_system_prompt():
    system = analyst.build_system_prompt()
    for mart in ("fct_orders", "fct_order_items", "fct_order_sellers", "dim_customers", "dim_products"):
        assert f"### marts.{mart}" in system, mart
    assert "{{" not in system and "doc(" not in system and "{% docs" not in system
    assert "The JSON envelope" in system and "Revenue means item price" in system
    assert system == analyst.build_system_prompt(), "prompt must be deterministic for caching"


def test_user_message_holds_question_and_schema_only():
    calls = FakeClient(lambda i, kw: SimpleNamespace(stop_reason="end_turn", usage=usage(),
                                                     content=[text_block(ANSWER)]))
    payload = {**PAYLOAD, "expected": {"n": 424242}, "gold_sql": "x.sql"}
    code, _, _ = run_main(calls, payload=payload)
    assert code == 0
    first = calls.calls[0]["first"]
    assert first["role"] == "user" and first["content"] == analyst.build_user_message(PAYLOAD)
    assert "424242" not in first["content"] and "gold" not in first["content"]
    assert PAYLOAD["question"] in first["content"] and "n: integer" in first["content"]


def test_allowed_docs():
    evals = [p for p in analyst.ALLOWED_DOCS if p.startswith("evals/")]
    assert evals == ["evals/ANSWER_FORMAT.md"], evals
    assert not any(p.endswith(".sql") for p in analyst.ALLOWED_DOCS)
    try:
        analyst.read_allowed(EVAL_FILE)
    except ValueError:
        pass
    else:
        raise AssertionError("read_allowed accepted a path outside ALLOWED_DOCS")


def test_agent_dir_never_mentions_eval_internals():
    forbidden = ["questions" + ".yaml", "evals" + "/gold", "gra" + "der", "run" + "_eval"]
    hits = []
    for path in sorted(HERE.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        text = path.read_text(errors="replace").lower()
        hits += [f"{path.relative_to(ROOT)}: {w}" for w in forbidden if w in text]
    assert not hits, hits


# ---------------------------------------------------------------------------------------------------------

def main():
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            print(f"       at line {tb.lineno}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
