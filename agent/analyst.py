"""Analyst agent v1: answers one business question about Olist orders from the marts.

Usage (from the repo root):
    echo '{"question": "...", "answer_schema": {...}}' | python agent/analyst.py

Reads one JSON object {"question", "answer_schema"} from stdin and prints the full response on stdout: the analysis in
prose, then the JSON envelope of evals/ANSWER_FORMAT.md as the last ```json block. At exit it always writes one line
to stderr:  USAGE {"input_tokens": n, "output_tokens": n, "tool_calls": n}

Exit codes: 0 answered (the envelope may still say cannot_answer), 1 failure (API error after retries, refusal,
truncated or empty answer), 2 bad input or missing OLIST_AGENT_API_KEY.

The model sees only the system prompt built from ALLOWED_DOCS, the question with its answer_schema, and the results
of its own run_sql calls (agent/sandbox.py).
"""
import json
import os
import random
import re
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sandbox as sandbox_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000          # adaptive thinking plus a long ranked answer, still fine without streaming
MAX_TOOL_CALLS = 10
MAX_TURNS = MAX_TOOL_CALLS + 5  # hard stop if the model keeps asking for tools after the budget
MAX_RETRIES = 3
BACKOFF_SECONDS = 2.0
API_TIMEOUT_SECONDS = 240
KEY_ENV = "OLIST_AGENT_API_KEY"

# Every file the agent's prompt is built from. Nothing else is ever read for the model.
BRIEF = "agent/BRIEF.md"
METRICS = "docs/metrics.md"
MARTS_YML = "dbt/models/marts/_marts.yml"
ANSWER_FORMAT = "evals/ANSWER_FORMAT.md"
DOC_BLOCK_FILES = (  # {% docs %} blocks referenced by doc() in the marts yml; only referenced blocks are used
    "dbt/models/marts/_marts__docs.md",
    "dbt/models/intermediate/_intermediate__docs.md",
)
ALLOWED_DOCS = (BRIEF, METRICS, MARTS_YML, *DOC_BLOCK_FILES, ANSWER_FORMAT)

BUDGET_MESSAGE = (f"Query budget used ({MAX_TOOL_CALLS} of {MAX_TOOL_CALLS}). Answer now from the results you have, "
                  "or answer with status cannot_answer if they are not enough. No more queries can run.")
OVER_BUDGET_RESULT = "ERROR: not run, the query budget is used up."

_DOC_BLOCK = re.compile(r"{%\s*docs\s+(\w+)\s*%}(.*?){%\s*enddocs\s*%}", re.DOTALL)
_DOC_REF = re.compile(r"{{\s*doc\(\s*['\"](\w+)['\"]\s*\)\s*}}")


class AgentError(Exception):
    """A failure that ends the run with exit code 1. The message never holds the API key or response bodies."""


# -- prompt ----------------------------------------------------------------------------------------------

def read_allowed(rel_path):
    if rel_path not in ALLOWED_DOCS:
        raise ValueError(f"{rel_path} is not in ALLOWED_DOCS")
    return (ROOT / rel_path).read_text()


def doc_blocks():
    blocks = {}
    for path in DOC_BLOCK_FILES:
        for name, body in _DOC_BLOCK.findall(read_allowed(path)):
            blocks[name] = " ".join(body.split())
    return blocks


def marts_docs():
    """Markdown for every model in the marts yml: description and columns, with doc() references resolved."""
    blocks = doc_blocks()

    def resolve(text):
        def sub(m):
            if m.group(1) not in blocks:
                raise ValueError(f"doc('{m.group(1)}') has no docs block in DOC_BLOCK_FILES")
            return blocks[m.group(1)]
        return " ".join(_DOC_REF.sub(sub, text or "").split())

    out = []
    for model in yaml.safe_load(read_allowed(MARTS_YML))["models"]:
        out.append(f"### marts.{model['name']}\n\n{resolve(model.get('description'))}\n")
        for col in model.get("columns", []):
            out.append(f"- `{col['name']}`: {resolve(col.get('description'))}")
        out.append("")
    return "\n".join(out)


def build_system_prompt():
    return "\n\n".join([
        "You are the Olist analyst agent described in the brief below. You answer one question per conversation. "
        "Use the run_sql tool to query the warehouse; every number in your answer must come from a query you ran. "
        "Your final message is the answer itself: a few sentences for a human reader, then the JSON envelope as the "
        "last ```json block, exactly as in the answer format at the end of this prompt.",
        "# Brief\n\n" + read_allowed(BRIEF).strip(),
        "# Metric definitions (docs/metrics.md)\n\n" + read_allowed(METRICS).strip(),
        "# Marts (the only tables you can query; unqualified names resolve to the marts schema)\n\n" + marts_docs().strip(),
        "# Answer format (evals/ANSWER_FORMAT.md)\n\n" + read_allowed(ANSWER_FORMAT).strip(),
    ])


def build_user_message(payload):
    schema = yaml.safe_dump(payload["answer_schema"], sort_keys=False, allow_unicode=True).strip()
    return f"Question:\n{payload['question']}\n\nanswer_schema:\n```yaml\n{schema}\n```"


# -- API -------------------------------------------------------------------------------------------------

def _retryable(exc):
    import anthropic
    if isinstance(exc, (anthropic.RateLimitError, anthropic.APIConnectionError)):
        return True
    return isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500


def _describe(exc):
    status = getattr(exc, "status_code", None)
    return type(exc).__name__ + (f" (status {status})" if status else "")


def call_with_retry(client, sleep, **kwargs):
    import anthropic
    for attempt in range(MAX_RETRIES + 1):
        try:
            return client.messages.create(**kwargs)
        except anthropic.APIError as exc:
            if not _retryable(exc):
                raise AgentError(f"API error: {_describe(exc)}") from None
            if attempt == MAX_RETRIES:
                raise AgentError(f"API error after {MAX_RETRIES + 1} attempts: {_describe(exc)}") from None
            sleep(BACKOFF_SECONDS * 2 ** attempt + random.uniform(0, 1))


# -- loop ------------------------------------------------------------------------------------------------

def _add_usage(usage, u):
    if u is None:
        return
    usage["input_tokens"] += sum(getattr(u, k, 0) or 0 for k in
                                 ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0


def run(payload, client, sandbox, usage, sleep=time.sleep, system=None):
    """Return the final response text. Updates usage in place, so it is complete even when this raises."""
    system = system if system is not None else build_system_prompt()
    messages = [{"role": "user", "content": build_user_message(payload)}]
    tool_choice = {"type": "auto"}
    for _ in range(MAX_TURNS):
        response = call_with_retry(
            client, sleep, model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=messages,
            tools=[sandbox_mod.TOOL], tool_choice=tool_choice, cache_control={"type": "ephemeral"})
        _add_usage(usage, response.usage)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "tool_use":
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                if usage["tool_calls"] < MAX_TOOL_CALLS:
                    usage["tool_calls"] += 1
                    sql = block.input.get("sql", "") if isinstance(block.input, dict) else ""
                    out = sandbox.run_sql(sql)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out,
                                    "is_error": out.startswith("ERROR")})
                else:
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": OVER_BUDGET_RESULT,
                                    "is_error": True})
            if usage["tool_calls"] >= MAX_TOOL_CALLS:
                results.append({"type": "text", "text": BUDGET_MESSAGE})
                tool_choice = {"type": "none"}
            messages.append({"role": "user", "content": results})
            continue

        if response.stop_reason == "end_turn":
            text = "\n\n".join(b.text for b in response.content if b.type == "text" and b.text.strip()).strip()
            if not text:
                raise AgentError("the model returned no text")
            return text
        if response.stop_reason == "refusal":
            raise AgentError("the model refused (stop_reason refusal)")
        raise AgentError(f"unexpected stop_reason {response.stop_reason}")
    raise AgentError(f"no final answer after {MAX_TURNS} turns")


# -- CLI -------------------------------------------------------------------------------------------------

def _default_client(api_key):
    import anthropic
    return anthropic.Anthropic(api_key=api_key, max_retries=0, timeout=API_TIMEOUT_SECONDS)


def main(stdin=None, stdout=None, stderr=None, env=None, client_factory=None, sandbox_factory=None,
         sleep=time.sleep):
    stdin, stdout, stderr = stdin or sys.stdin, stdout or sys.stdout, stderr or sys.stderr
    env = os.environ if env is None else env
    usage = {"input_tokens": 0, "output_tokens": 0, "tool_calls": 0}
    sandbox = None
    try:
        try:
            payload = json.loads(stdin.read())
            if not isinstance(payload, dict) or not {"question", "answer_schema"} <= set(payload):
                raise ValueError("need an object with question and answer_schema")
        except ValueError as exc:
            print(f"analyst: bad input: {exc}", file=stderr)
            return 2
        api_key = env.get(KEY_ENV)
        if not api_key:
            print(f"analyst: {KEY_ENV} is not set", file=stderr)
            return 2
        try:
            client = (client_factory or _default_client)(api_key)
            sandbox = (sandbox_factory or sandbox_mod.Sandbox)()
            text = run(payload, client, sandbox, usage, sleep=sleep)
        except AgentError as exc:
            print(f"analyst: {exc}", file=stderr)
            return 1
        except Exception as exc:  # only database errors (e.g. a lock) show their text; others could echo request data
            detail = f": {sandbox_mod._one_line(exc)}" if isinstance(exc, sandbox_mod.duckdb.Error) else ""
            print(f"analyst: failed: {type(exc).__name__}{detail}", file=stderr)
            return 1
        print(text, file=stdout)
        return 0
    finally:
        if sandbox is not None:
            sandbox.close()
        print("USAGE " + json.dumps(usage), file=stderr)
        stderr.flush()


if __name__ == "__main__":
    sys.exit(main())
