"""Tests for the analyst agent: sandbox, model loop (fake client, no API calls), prompt and isolation.

Run from the repo root:  python agent/test_agent.py   (or pytest agent/test_agent.py)
Needs warehouse.duckdb with no writer holding the lock.
"""
import io
import json
import sys
import tempfile
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

def usage(i=100, o=10, cr=0, cw=0):
    return SimpleNamespace(input_tokens=i, output_tokens=o, cache_read_input_tokens=cr, cache_creation_input_tokens=cw)


def text_block(t):
    return SimpleNamespace(type="text", text=t)


def tool_block(n, sql):
    return SimpleNamespace(type="tool_use", id=f"tu_{n}", name="run_sql", input={"sql": sql})


ANSWER = 'There were 1 orders.\n\n```json\n{"status": "answered", "assumptions": [], "answer": {"n": 1}}\n```'


class FakeStream:
    """Stands in for the SDK's MessageStream: a context manager whose get_final_message() returns the response."""

    def __init__(self, produce):
        self.produce = produce

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.produce()  # a script exception raises here, as a mid-stream error would


class FakeClient:
    """Streaming only: there is no create(), so a non-streaming call fails the test."""

    def __init__(self, script):
        self.script = script  # function(call_index, kwargs) -> response, or raises
        self.calls = []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append({"tool_choice": kwargs["tool_choice"], "n_messages": len(kwargs["messages"]),
                           "last": kwargs["messages"][-1], "model": kwargs["model"], "system": kwargs["system"],
                           "first": kwargs["messages"][0], "max_tokens": kwargs["max_tokens"],
                           "cache_control": kwargs.get("cache_control")})
        i = len(self.calls) - 1
        return FakeStream(lambda: self.script(i, kwargs))


PAYLOAD = {"question": "How many orders?", "answer_schema": {"status": "answered | cannot_answer",
                                                               "answer": {"n": "integer"}}}


def run_main(client, env=None, payload=PAYLOAD, sleeps=None):
    out, err = io.StringIO(), io.StringIO()
    code = analyst.main(stdin=io.StringIO(json.dumps(payload)), stdout=out, stderr=err,
                        env={analyst.KEY_ENV: "sk-test-secret-123"} if env is None else env,
                        client_factory=lambda key: client, sandbox_factory=lambda: sandbox.Sandbox(timeout=5),
                        sleep=(sleeps.append if sleeps is not None else (lambda s: None)))
    return code, out.getvalue(), err.getvalue()


CONFIG_KEYS = ("model", "docs", "brief")
DEFAULT_CONFIG = {"model": "claude-sonnet-5-5", "docs": "full", "brief": "full"}


def usage_line(err, config=False):
    """The counts of the single USAGE line (it must also hold the config keys); with config=True, the config."""
    lines = [ln for ln in err.splitlines() if ln.startswith("USAGE ")]
    assert len(lines) == 1 and err.rstrip().endswith(lines[0]), f"USAGE line: {err!r}"
    data = json.loads(lines[0][len("USAGE "):])
    assert set(data) == {"input_tokens", "output_tokens", "tool_calls", *CONFIG_KEYS}, data
    return {k: data[k] for k in (CONFIG_KEYS if config else ("input_tokens", "output_tokens", "tool_calls"))}


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


# -- trace ------------------------------------------------------------------------------------------------

SECRET = "sk-test-secret-123"


def one_query_script(i, kw):
    if i == 0:
        return SimpleNamespace(stop_reason="tool_use", usage=usage(),
                               content=[text_block("Counting."), tool_block(1, "SELECT count(*) AS n FROM fct_orders")])
    return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])


def read_trace(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def test_trace_written_when_env_set():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        client = FakeClient(one_query_script)
        code, out, err = run_main(client, env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: str(path)})
        assert code == 0, err
        events = read_trace(path)
        assert [e["event"] for e in events] == ["text", "sql", "tool_result", "text", "final"], events
        assert events[0] == {"event": "text", "turn": 1, "text": "Counting."}
        assert events[1] == {"event": "sql", "turn": 1, "tool_use_id": "tu_1",
                             "sql": "SELECT count(*) AS n FROM fct_orders"}
        sent = client.calls[1]["last"]["content"][0]  # the tool result exactly as the model received it
        assert events[2] == {"event": "tool_result", "tool_use_id": "tu_1", "content": sent["content"],
                             "is_error": sent["is_error"]}
        assert events[2]["content"].startswith("n\n99441")
        assert events[4] == {"event": "final", "text": out.strip()}


def test_trace_records_sql_errors_and_budget():
    def script(i, kw):
        if kw["tool_choice"] == {"type": "none"}:
            return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])
        return SimpleNamespace(stop_reason="tool_use", usage=usage(),
                               content=[tool_block(f"{i}_{k}", "SELECT * FROM nope") for k in range(3)])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        code, _, err = run_main(FakeClient(script), env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: str(path)})
        assert code == 0, err
        events = read_trace(path)
        results = [e for e in events if e["event"] == "tool_result"]
        assert len(results) == 12 and all(r["is_error"] for r in results)
        assert results[0]["content"].startswith("ERROR: unknown table")
        assert [r["content"] == analyst.OVER_BUDGET_RESULT for r in results[-3:]] == [False, True, True]
        assert sum(e["event"] == "sql" for e in events) == 12
        assert [e["event"] for e in events[-3:]] == ["budget_message", "text", "final"]


def test_no_trace_without_env():
    with tempfile.TemporaryDirectory() as tmp:
        code, _, err = run_main(FakeClient(one_query_script))
        assert code == 0, err
        code, _, err = run_main(FakeClient(one_query_script), env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: ""})
        assert code == 0, err
        assert list(Path(tmp).iterdir()) == []


def test_trace_file_unopenable_exits_2():
    with tempfile.TemporaryDirectory() as tmp:
        bad = Path(tmp) / "missing_dir" / "trace.jsonl"
        code, out, err = run_main(FakeClient(None), env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: str(bad)})
        assert code == 2 and out == "" and "cannot open trace file" in err and str(bad) not in err


def test_key_never_in_trace():
    scripts = (one_query_script,
               lambda i, kw: SimpleNamespace(stop_reason="refusal", usage=usage(), content=[text_block("no")]),
               lambda i, kw: (_ for _ in ()).throw(RuntimeError(SECRET)))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        for script in scripts:
            run_main(FakeClient(script), env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: str(path)})
        text = path.read_text()
        assert SECRET not in text
        events = read_trace(path)
        errors = [e["message"] for e in events if e["event"] == "error"]
        assert errors == ["the model refused (stop_reason refusal)", "failed: RuntimeError"], errors


def test_cannot_answer_exits_zero():
    refusal = ('The data has no cost column, so margin cannot be computed.\n\n```json\n'
               '{"status": "cannot_answer", "assumptions": [], "answer": null, '
               '"reason": "No cost data in the marts."}\n```')
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        client = FakeClient(lambda i, kw: SimpleNamespace(stop_reason="end_turn", usage=usage(),
                                                          content=[text_block(refusal)]))
        code, out, err = run_main(client, env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: str(path)})
        assert code == 0, err
        assert out.strip() == refusal
        assert read_trace(path)[-1] == {"event": "final", "text": refusal}


# -- streaming and output limit ---------------------------------------------------------------------------

def test_streamed_answer_returned_and_traced():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        client = FakeClient(one_query_script)
        code, out, err = run_main(client, env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: str(path)})
        assert code == 0, err
        assert out.strip() == ANSWER
        assert len(client.calls) == 2
        assert all(c["max_tokens"] == analyst.MAX_TOKENS == 128000 for c in client.calls)
        assert all(c["cache_control"] == {"type": "ephemeral"} for c in client.calls)
        events = read_trace(path)
        assert events[-1] == {"event": "final", "text": ANSWER}
        assert [e["event"] for e in events] == ["text", "sql", "tool_result", "text", "final"]


def test_max_tokens_stop():
    def script(i, kw):
        if i == 0:
            return SimpleNamespace(stop_reason="tool_use", usage=usage(o=30),
                                   content=[tool_block(1, "SELECT count(*) AS n FROM fct_orders")])
        return SimpleNamespace(stop_reason="max_tokens", usage=usage(o=analyst.MAX_TOKENS),
                               content=[text_block("A very long list that never")])
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        code, out, err = run_main(FakeClient(script), env={analyst.KEY_ENV: SECRET, analyst.TRACE_ENV: str(path)})
        assert code == 1 and out == ""
        assert "output limit hit" in err and "max_tokens" in err, err
        message = f"output limit hit: one model turn used all {analyst.MAX_TOKENS} max_tokens (stop_reason max_tokens)"
        assert f"analyst: {message}" in err
        events = read_trace(path)
        assert events[-1] == {"event": "error", "message": message}, events[-1]
        assert not any(e["event"] == "final" for e in events)
        assert usage_line(err) == {"input_tokens": 200, "output_tokens": 30 + analyst.MAX_TOKENS, "tool_calls": 1}


def test_usage_totals_with_streaming():
    def script(i, kw):
        if i == 0:
            return SimpleNamespace(stop_reason="tool_use", usage=usage(i=7, o=40, cr=0, cw=5000),
                                   content=[tool_block(1, "SELECT 1 AS n")])
        if i == 1:
            return SimpleNamespace(stop_reason="tool_use", usage=usage(i=12, o=55, cr=5000, cw=300),
                                   content=[tool_block(2, "SELECT 2 AS n")])
        return SimpleNamespace(stop_reason="end_turn", usage=usage(i=20, o=900, cr=5300, cw=0),
                               content=[text_block(ANSWER)])
    code, _, err = run_main(FakeClient(script))
    assert code == 0, err
    assert usage_line(err) == {"input_tokens": 7 + 5000 + 12 + 5000 + 300 + 20 + 5300,
                               "output_tokens": 40 + 55 + 900, "tool_calls": 2}


def test_midstream_overloaded_is_retried():
    import anthropic
    import httpx2

    def script(i, kw):
        if i == 0:  # an error event inside the stream: the HTTP status of the stream response is 200
            raise anthropic.APIStatusError(
                "overloaded", response=httpx2.Response(200, request=httpx2.Request("POST", "https://api.invalid")),
                body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
        return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])
    sleeps = []
    client = FakeClient(script)
    code, out, err = run_main(client, sleeps=sleeps)
    assert code == 0, err
    assert out.strip() == ANSWER and len(client.calls) == 2 and len(sleeps) == 1

    def invalid(i, kw):  # a non-retryable error event is not retried
        raise anthropic.APIStatusError(
            "bad", response=httpx2.Response(200, request=httpx2.Request("POST", "https://api.invalid")),
            body={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}})
    client = FakeClient(invalid)
    code, _, err = run_main(client)
    assert code == 1 and len(client.calls) == 1 and "invalid_request_error" in err, err


# -- prompt and isolation ---------------------------------------------------------------------------------

def test_system_prompt():
    system = analyst.build_system_prompt()
    for mart in ("fct_orders", "fct_order_items", "fct_order_sellers", "dim_customers", "dim_products"):
        assert f"### marts.{mart}" in system, mart
    assert "{{" not in system and "doc(" not in system and "{% docs" not in system
    assert "The JSON envelope" in system and "Revenue means item price" in system
    assert system == analyst.build_system_prompt(), "prompt must be deterministic for caching"


MARKERS = ("<!-- rules:start -->", "<!-- rules:end -->", "<!-- docs:start -->", "<!-- docs:end -->")
SCHEMA_LINE = "You know the table and column descriptions of the marts."
NONE_LINE = "You have no documents; discover tables and columns with information_schema."


def all_prompts():
    return {(d, b): analyst.build_system_prompt(d, b) for d in analyst.DOCS_MODES for b in analyst.BRIEF_MODES}


def old_build_system_prompt():
    """A literal copy of the prompt formula before ablations; the brief is BRIEF.md without its marker lines."""
    brief = "".join(ln for ln in (ROOT / analyst.BRIEF).read_text().splitlines(keepends=True)
                    if ln.strip() not in MARKERS)
    return "\n\n".join([
        "You are the Olist analyst agent described in the brief below. You answer one question per conversation. "
        "Use the run_sql tool to query the warehouse; every number in your answer must come from a query you ran. "
        "Your final message is the answer itself: a few sentences for a human reader, then the JSON envelope as the "
        "last ```json block, exactly as in the answer format at the end of this prompt.",
        "# Brief\n\n" + brief.strip(),
        "# Metric definitions (docs/metrics.md)\n\n" + (ROOT / analyst.METRICS).read_text().strip(),
        "# Marts (the only tables you can query; unqualified names resolve to the marts schema)\n\n"
        + analyst.marts_docs().strip(),
        "# Answer format (evals/ANSWER_FORMAT.md)\n\n" + (ROOT / analyst.ANSWER_FORMAT).read_text().strip(),
    ])


def answer_once():
    return FakeClient(lambda i, kw: SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)]))


def metrics_lines():
    lines = [ln.strip() for ln in (ROOT / analyst.METRICS).read_text().splitlines() if len(ln.strip()) >= 30]
    assert len(lines) > 50, len(lines)
    return lines


def marts_doc_texts():
    """Model headings and every model or column description of the marts docs, doc() blocks resolved."""
    texts = []
    for ln in analyst.marts_docs().splitlines():
        ln = ln.strip()
        if ln.startswith("- `"):
            ln = ln.split("`: ", 1)[1]
        if len(ln) >= 20:
            texts.append(ln)
    assert len(texts) > 50, len(texts)
    return texts


def answer_format_text():
    return (ROOT / analyst.ANSWER_FORMAT).read_text().strip()


def test_default_prompt_unchanged():
    expected = old_build_system_prompt()
    assert analyst.build_system_prompt() == expected
    assert analyst.build_system_prompt("full", "full") == expected
    assert analyst.RULES_START not in expected and analyst.RULES_END not in expected and "<!--" not in expected
    for env in ({}, {analyst.MODEL_ENV: "", analyst.DOCS_ENV: "", analyst.BRIEF_ENV: ""}):
        client = answer_once()
        code, _, err = run_main(client, env={analyst.KEY_ENV: "sk-test-secret-123", **env})
        assert code == 0, err
        assert client.calls[0]["system"] == expected, "main sends a different default prompt"
        assert client.calls[0]["model"] == "claude-sonnet-5-5"
        assert usage_line(err, config=True) == DEFAULT_CONFIG


def test_schema_mode_has_no_metrics_text():
    system = analyst.build_system_prompt("schema")
    hits = [ln for ln in metrics_lines() if ln in system]
    assert not hits, hits[:3]
    assert "# Metric definitions" not in system
    missing = [t for t in marts_doc_texts() if t not in system]
    assert not missing, missing[:3]
    for mart in ("fct_orders", "fct_order_items", "fct_order_sellers", "dim_customers", "dim_products"):
        assert f"### marts.{mart}" in system, mart
    assert "{{" not in system and "doc(" not in system
    assert answer_format_text() in system and "Revenue means item price" in system


def test_none_mode_has_no_document_text():
    system = analyst.build_system_prompt("none")
    hits = [ln for ln in metrics_lines() if ln in system]
    assert not hits, hits[:3]
    hits = [t for t in marts_doc_texts() if t in system]
    assert not hits, hits[:3]
    for body in analyst.doc_blocks().values():
        assert body not in system, body[:80]
    assert "### marts." not in system and "# Marts" not in system and "# Metric definitions" not in system
    assert answer_format_text() in system and "# Brief" in system
    assert system == "\n\n".join([system.split("\n\n# Brief")[0], "# Brief\n\n" + analyst.brief_text("full", "none").strip(),
                                   "# Answer format (evals/ANSWER_FORMAT.md)\n\n" + answer_format_text()])


def test_minimal_brief_drops_rules():
    full, minimal = analyst.brief_text("full"), analyst.brief_text("minimal")
    rules = full.split("## 4. Rules\n", 1)[1].split("\n## 5.", 1)[0]
    kept = "- Follow evals/ANSWER_FORMAT.md exactly. The JSON envelope is the last thing in the response."
    dropped = [ln for ln in rules.splitlines() if ln.strip() and ln != kept]
    assert len(dropped) >= 10, dropped
    for docs in analyst.DOCS_MODES:
        system = analyst.build_system_prompt(docs, "minimal")
        brief = system.split("# Brief\n\n", 1)[1].split("\n\n# ", 1)[0]
        assert brief == analyst.brief_text("minimal", docs).strip()
        hits = [ln for ln in dropped if ln in brief]
        assert not hits, hits
        assert kept in brief and "<!--" not in brief
        for heading in ("## 1. Role", "## 2. What it knows", "## 3. What it can do", "## 4. Rules", "## 5."):
            assert heading in brief, heading
        assert "One tool for v1: run_sql" in brief and "Limits: 10 queries per question" in brief
        assert "in the format of evals/ANSWER_FORMAT.md" in brief
    assert "Revenue means item price" not in minimal and "Revenue means item price" in full


def test_env_config_reaches_api():
    def script(i, kw):
        if i == 0:
            return SimpleNamespace(stop_reason="tool_use", usage=usage(),
                                   content=[tool_block(1, "SELECT count(*) AS n FROM fct_orders")])
        return SimpleNamespace(stop_reason="end_turn", usage=usage(), content=[text_block(ANSWER)])
    client = FakeClient(script)
    env = {analyst.KEY_ENV: "sk-test-secret-123", analyst.MODEL_ENV: "claude-test-x",
           analyst.DOCS_ENV: "none", analyst.BRIEF_ENV: "minimal"}
    code, _, err = run_main(client, env=env)
    assert code == 0, err
    assert [c["model"] for c in client.calls] == ["claude-test-x"] * 2
    assert all(c["system"] == analyst.build_system_prompt("none", "minimal") for c in client.calls)
    assert all(NONE_LINE in c["system"] and "docs/metrics.md" not in c["system"] for c in client.calls)
    assert usage_line(err, config=True) == {"model": "claude-test-x", "docs": "none", "brief": "minimal"}
    assert usage_line(err) == {"input_tokens": 200, "output_tokens": 20, "tool_calls": 1}


def test_invalid_config_exits_2():
    bad = [{analyst.MODEL_ENV: "gpt-4"}, {analyst.MODEL_ENV: "claude-"}, {analyst.MODEL_ENV: "claude sonnet"},
           {analyst.MODEL_ENV: "my-claude-model"}, {analyst.DOCS_ENV: "bogus"}, {analyst.DOCS_ENV: "FULL"},
           {analyst.BRIEF_ENV: "bogus"}]
    for env in bad:
        client = FakeClient(None)
        code, out, err = run_main(client, env={analyst.KEY_ENV: "sk-test-secret-123", **env})
        assert code == 2 and not client.calls and not out, (env, code, err)
        assert "bad config" in err and next(iter(env)) in err, err
        assert usage_line(err, config=True) == dict.fromkeys(CONFIG_KEYS), err
        assert usage_line(err) == {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0}


def test_markers_never_in_prompt():
    assert (analyst.RULES_START, analyst.RULES_END, analyst.DOCS_START, analyst.DOCS_END) == MARKERS
    brief_md = (ROOT / analyst.BRIEF).read_text()
    assert all(m in brief_md for m in MARKERS), "BRIEF.md lost a marker"
    for key, system in all_prompts().items():
        hits = [m for m in MARKERS if m in system]
        assert not hits and "<!--" not in system and "-->" not in system, (key, hits)


def test_full_docs_prompt_matches_previous_default():
    prompts = all_prompts()
    old = old_build_system_prompt()
    assert prompts[("full", "full")] == old
    assert SCHEMA_LINE not in old and NONE_LINE not in old
    assert "- docs/metrics.md (metric definitions) and the dbt model docs for the marts." in old
    assert "window rules defined in\n  docs/metrics.md. Never use the current date." in old
    # full + minimal: the old prompt with exactly the rules lines removed (docs markers change nothing)
    rules = [ln for block in (ROOT / analyst.BRIEF).read_text().split(analyst.RULES_START)[1:]
             for ln in block.split(analyst.RULES_END)[0].splitlines() if ln.strip() and ln.strip() not in MARKERS]
    expected = old
    for ln in rules:
        assert expected.count(ln + "\n") == 1, ln
        expected = expected.replace(ln + "\n", "", 1)
    assert prompts[("full", "minimal")] == expected


def test_ablated_prompts_do_not_mention_metrics_doc():
    for (docs, brief), system in all_prompts().items():
        if docs == "full":
            continue
        assert "docs/metrics.md" not in system, (docs, brief)
        assert "dbt model docs" not in system and "window rules defined in" not in system, (docs, brief)
        if docs == "none":
            assert "metrics.md" not in system, (docs, brief)


def test_docs_replacement_lines():
    for (docs, brief), system in all_prompts().items():
        section_2 = system.split("## 2. What it knows\n", 1)[1].split("\n## 3.", 1)[0]
        lines = section_2.splitlines()
        want = {"full": None, "schema": SCHEMA_LINE, "none": NONE_LINE}[docs]
        for line in (SCHEMA_LINE, NONE_LINE):
            assert system.count(line) == (1 if line == want else 0), (docs, brief, line)
        if want:
            assert lines[0] == "- " + want, (docs, brief, lines[0])
        else:
            assert lines[0].startswith("- docs/metrics.md"), (docs, brief)


def test_brief_markers_validated():
    original = analyst.read_allowed
    good = (ROOT / analyst.BRIEF).read_text()
    broken = {
        "docs end dropped": good.replace(analyst.DOCS_END + "\n", "", 1),
        "docs start dropped": good.replace(analyst.DOCS_START + "\n", "", 1),
        "no docs in section 2": good.replace("## 2. What it knows", "## 2b What it knows"),
        "rules end dropped": good.replace(analyst.RULES_END + "\n", "", 1),
    }
    try:
        for label, text in broken.items():
            assert text != good, label
            analyst.read_allowed = lambda rel, t=text: t if rel == analyst.BRIEF else original(rel)
            for docs in analyst.DOCS_MODES:
                try:
                    analyst.brief_text("full", docs)
                except ValueError:
                    continue
                raise AssertionError(f"{label}: accepted with docs={docs}")
    finally:
        analyst.read_allowed = original


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
