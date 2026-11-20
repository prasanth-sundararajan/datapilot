"""DataPilot agent team: Planner -> Coder -> Executor -> Reviewer, with a self-correcting loop.

Routing (default "rules" mode, a SelectorGroupChat with a deterministic selector function):
    user/question -> Planner -> Coder -> Executor
    Executor  --error-->  Coder        (Coder reads the traceback and rewrites)
    Executor  --ok----->  Reviewer
    Reviewer  --feedback-> Coder       (Coder applies the feedback)
    Reviewer  --APPROVED-> stop
"llm" mode (stretch goal) lets the LLM pick the next speaker instead.

The run stops on TextMentionTermination('APPROVED') from the Reviewer, or MaxMessageTermination(12).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence

from autogen_agentchat.agents import AssistantAgent, CodeExecutorAgent
from autogen_agentchat.base import Response
from autogen_agentchat.messages import TextMessage
from autogen_agentchat.conditions import FunctionalTermination, MaxMessageTermination, TextMentionTermination, TimeoutTermination
from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat
from autogen_core import CancellationToken
from autogen_core.code_executor import CodeBlock, CodeResult

import guardrails as gr

ROOT = Path(__file__).resolve().parent

def _sandbox_root() -> Path:
    """Sandbox folder for AI-written code. AutoGen writes tmp_code_<64-char-hash>.py files there, so on
    Windows a deep project path can exceed MAX_PATH (260); fall back to a short folder in that case."""
    env = os.getenv("SANDBOX_DIR")
    if env:
        return Path(env).expanduser().resolve()
    default = ROOT / "sandbox"
    if sys.platform == "win32" and len(str(default)) > 140:
        return Path.home() / "datapilot_sandbox"
    return default


SANDBOX_ROOT = _sandbox_root()

OK_TAG = "STATUS: OK"
ERR_TAG = "STATUS: ERROR"

# ---------------------------------------------------------------- prompts
PLANNER_PROMPT = """You are the Planner in a data-analysis team.
You get a business question and the dataset schema (column names, types, a few sample rows).
Write a short numbered analysis plan of 3-6 steps. Use the exact column names.
Spell out formulas (e.g. revenue = units * unit_price * (1 - discount)), the grouping/filters,
which numbers the final answer must report, and which 1-3 charts to make (bar or line charts, not pies).
For "which X dropped/grew most" questions: first compare TOTALS per X across the two periods
(before value, after value, absolute change, % change). For "which Y caused it": split that X's total change
by Y and report each Y's share (%) of the total change, then the top items inside the main Y.
Do NOT write code and do NOT guess any numbers. Keep it under 150 words.
If you are asked to speak again later in the conversation (e.g. after Reviewer feedback), reply with
one line: "Plan unchanged - Coder, apply the latest feedback." unless the plan itself was wrong.""" + gr.UNTRUSTED_DATA_RULES

CODER_PROMPT = """You are the Coder. Turn the Planner's plan into ONE complete, self-contained Python
script inside a single ```python code block. The Executor runs it in the current folder.
Rules:
- Read the data file exactly as described in the schema (relative path, current folder).
- Use pandas and matplotlib only. Start with: import warnings; warnings.filterwarnings("ignore");
  import matplotlib; matplotlib.use("Agg"). Compute derived columns on the full DataFrame before filtering.
- Answer EVERY part of the question in the printout (e.g. "which region caused it" needs each region's
  change and share % of the total change).
- print() every number the answer needs, with clear labels. Money in rupees: print the raw value
  and the lakh value (1 L = 100,000), e.g. "Electronics Aug: Rs 1,840,000 (18.4 L)".
- Save each chart as a PNG in the current folder with a descriptive file name (e.g. category_trend.png).
  Use bar or line charts (no pie charts). EVERY chart must have a title, an x-axis label and a y-axis
  label that includes units (e.g. "Revenue (Rs lakh)"). Plotted values must match the unit in the label:
  divide by 1e5 before plotting when the label says lakh. Call plt.tight_layout(), savefig(dpi=120), plt.close().
- Real exports are messy: text numbers ("₹1,999", "10%"), mixed date formats (day-first), stray spaces and
  different capitalisation in names. Clean them explicitly. Right after cleaning the FULL table (before any
  filtering to months/regions in the question) ALWAYS call the
  sandbox's data check (it raises an error if more than 2% of rows were lost, dates did not parse, or a name is
  spelled two ways):
      from _dp_quality_gate import data_check
      data_check(raw_rows=len(raw_df), df=clean_df, group_cols=["region", "category"], date_col="order_date",
                 date_raw=raw_df["order_date"])
  (use your real column names; list every text column you group or join by; keep the raw_df index in clean_df).
- Print a clear SUMMARY block at the end with the headline numbers (before, after, change, % change, shares).
- Deterministic results: no randomness, sort outputs.
- Never use input(), the network, subprocess, or delete/modify files other than the PNGs you create.
The sandbox checks every chart you save: a missing title, axis label or y-axis unit raises an error.
When the Executor reports an error: start with "Fixed:" and say in ONE line what was wrong, then resend the
FULL corrected script. (Tracebacks are 1 line off: the sandbox adds one import line at the top.)
When the Reviewer gives feedback: apply it and resend the FULL script. Never send partial snippets.
Never reply without a ```python block: you are only called when code must be written or fixed.
The sandbox statically checks your code: only pandas, numpy, matplotlib and the standard math/date/text modules
may be imported; os, sys, subprocess, network, eval/exec, absolute paths and file deletes are blocked.""" + gr.UNTRUSTED_DATA_RULES

REVIEWER_PROMPT = """You are the Reviewer. You check the latest Executor output (and the Coder's script)
before the user sees anything. Check:
1. The last Executor run says STATUS: OK.
2. The printed results answer EVERY part of the user's question. For comparisons: the TOTAL before and after
   values, the absolute change and the % change must be printed. For "what caused it": each contributor's share
   (%) of the TOTAL change must be printed. A breakdown number must never be presented as the total.
3. Numbers are sensible and consistent (parts add up to the total, % changes match the before/after values).
   Any share outside 0-100%, or shares of one total that do not add up to about 100%, are WRONG: send FEEDBACK.
   A contribution must be a change/gap amount, never a raw revenue total.
4. Every chart in the script has a title, x and y axis labels, and units in the y label. No pie charts.
5. The DATA CHECK block is present and clean: group values have no duplicates that differ only by case or
   spaces (e.g. "North" and "north "), no more than 2% of rows dropped, dates parsed for the whole range.
   When joining tables, both sides use the same cleaned keys. Otherwise the numbers are wrong: send FEEDBACK.
Feedback is ONLY for blocking problems: a failed run, a dirty DATA CHECK, a part of the question with no printed number,
numbers that contradict each other, a breakdown shown as the total, or a chart missing title/axis labels/units.
NEVER send feedback about sign conventions (a drop may be printed as negative), rounding, wording, formatting,
or extra analysis you would like. You can fix presentation yourself in the final answer
(e.g. write "fell by Rs 19.7 L" for a printed change of -19.7 L). Never repeat feedback that was already fixed.

ALWAYS start your reply with one line per part of the user's question:
CHECK <part of the question> -> <the exact printed number that answers it, or MISSING>
If any part is MISSING (e.g. the question asks "which region caused it" but no region shares were printed),
or anything above fails, continue with a line starting "FEEDBACK:" and list concrete fixes for the Coder.
Do not write the word that signals approval when giving feedback.
If the last Executor message is an error, reply "FEEDBACK: fix the error above." (this only happens in
round-robin mode).

If every CHECK has a number and everything passes, after the CHECK lines write the final answer for a
business owner, using ONLY numbers printed by the Executor, in exactly this format:
ANSWER
<1-3 sentences with the exact numbers>
WHERE IT CAME FROM
<1-3 sentences explaining the cause / breakdown>
FILES
<chart png names> | analysis.py
SUGGESTED NEXT QUESTION
<one useful follow-up question that this data can answer>
APPROVED

The last line of an approval MUST be the single word APPROVED.""" + gr.UNTRUSTED_DATA_RULES

SELECTOR_PROMPT = """You are routing a data-analysis team. Roles:
{roles}

Rules: after a user question -> Planner. After Planner -> Coder. After Coder -> Executor.
After Executor: if it contains "STATUS: ERROR" or no code was found -> Coder, otherwise -> Reviewer.
After Reviewer feedback -> Coder.

Conversation:
{history}

Pick the next speaker from {participants}. Reply with the name only."""


# ---------------------------------------------------------------- state + sandbox executor
@dataclass
class RunState:
    run_dir: Path
    data_file: Path | None = None
    schema: str = ""
    executions: int = 0
    errors: int = 0
    fixed_after_error: int = 0
    last_ok_code: str | None = None
    last_ok_output: str | None = None
    last_ok_charts: List[str] = field(default_factory=list)
    last_status: str | None = None
    question_index: int = 0


# Static code checks (AST allow/deny list) live in guardrails.check_code.


GATE_MODULE = "_dp_quality_gate"
GATE_IMPORT = f"import {GATE_MODULE}  # DataPilot sandbox: chart quality check\n"
# Loaded before every run in the sandbox. Saving a chart without a title, axis labels or units raises a real
# error, so the Coder has to fix it, exactly like any other bug. Not part of analysis.py.
GATE_SOURCE = gr.RUNTIME_GUARD_SOURCE + r'''
import matplotlib
matplotlib.use("Agg")
from matplotlib.figure import Figure

_UNIT_HINTS = ("(", "rs", "₹", "%", "lakh", "crore", "inr", "units", "count", "qty", "number", "orders")
_orig_savefig = Figure.savefig


def _checked_savefig(self, fname, *args, **kwargs):
    suptitle = self._suptitle.get_text() if getattr(self, "_suptitle", None) else ""
    for ax in self.axes:
        if not ax.has_data():
            continue
        problems = []
        if not (ax.get_title() or suptitle):
            problems.append("no title")
        if not ax.get_xlabel():
            problems.append("no x-axis label")
        ylabel = ax.get_ylabel()
        if not ylabel:
            problems.append("no y-axis label")
        elif not any(h in ylabel.lower() for h in _UNIT_HINTS):
            problems.append(f"y-axis label {ylabel!r} has no units, e.g. 'Revenue (Rs lakh)'")
        if problems:
            raise ValueError(f"Chart quality check failed for {fname}: " + "; ".join(problems)
                             + ". Every chart needs a title, x and y axis labels, and units on the y axis.")
    return _orig_savefig(self, fname, *args, **kwargs)


Figure.savefig = _checked_savefig

# Keep a pristine copy of every table the script loads, so data_check can compare cleaned dates to the originals.
import pandas as _pd
_RAW_TABLES = []
_orig_read_csv, _orig_read_excel = _pd.read_csv, _pd.read_excel


def _remember(obj):
    for t in (obj.values() if isinstance(obj, dict) else [obj]):
        if isinstance(t, _pd.DataFrame):
            _RAW_TABLES.append(t.copy())
    return obj


_pd.read_csv = lambda *a, **k: _remember(_orig_read_csv(*a, **k))
_pd.read_excel = lambda *a, **k: _remember(_orig_read_excel(*a, **k))
''' + "\n" + r'''
def data_check(raw_rows, df, group_cols=(), date_col=None, max_drop=0.02, date_raw=None):
    """DataPilot data check: prints a DATA CHECK block and raises if the cleaning looks wrong.
    date_raw: the original (uncleaned) date column, used to catch day/month swaps."""
    import pandas as pd
    used = len(df)
    dropped = raw_rows - used
    print("DATA CHECK")
    print(f"  rows loaded: {raw_rows:,} | rows used: {used:,} | dropped: {dropped:,}")
    problems = []
    if raw_rows and dropped / raw_rows > max_drop:
        problems.append(f"{dropped:,} of {raw_rows:,} rows ({dropped / raw_rows:.0%}) were dropped, more than "
                        f"{max_drop:.0%}. If you filtered rows on purpose (e.g. to the months in the question), "
                        "call data_check on the full cleaned data BEFORE filtering. Otherwise the cleaning is "
                        "losing data (e.g. mixed date formats need pd.to_datetime(col, format='mixed', dayfirst=True))")
    for col in group_cols:
        vals = df[col].dropna().astype(str).unique()
        print(f"  {col}: {sorted(vals)[:25]}")
        seen = {}
        for v in vals:
            seen.setdefault(v.strip().lower(), []).append(v)
        dups = [v for v in seen.values() if len(v) > 1]
        if dups:
            problems.append(f"column {col!r} has the same value spelled differently {dups[:3]}: "
                            "normalise it, e.g. .str.strip().str.title()")
    if date_col is not None:
        d = df[date_col]
        if not pd.api.types.is_datetime64_any_dtype(d):
            problems.append(f"column {date_col!r} is not a parsed datetime")
        else:
            print(f"  {date_col}: {d.min()} to {d.max()} ({int(d.isna().sum())} unparsed)")
            if d.isna().any():
                problems.append(f"{int(d.isna().sum())} values in {date_col!r} could not be parsed")
            if date_raw is None:  # use the sandbox's pristine copy of the loaded file
                for t in globals().get("_RAW_TABLES", []):
                    cands = [c for c in t.columns if t[c].dtype == object and
                             (c == date_col or "date" in str(c).lower())]
                    if cands and df.index.isin(t.index).all():
                        date_raw = t[cands[0]]
                        break
            if date_raw is not None:
                raw = date_raw.reindex(df.index).astype(str).str.strip()
                iso = raw.str.match(r"^\d{4}-\d{2}-\d{2}$") & d.notna()
                swapped = int((d[iso].dt.strftime("%Y-%m-%d") != raw[iso].str[:10]).sum())
                if swapped:
                    problems.append(f"{swapped:,} ISO dates (YYYY-MM-DD) were parsed with day and month swapped "
                                    "(e.g. 2026-09-11 became 9 Nov): parse the whole column at once with "
                                    "pd.to_datetime(col, format='mixed', dayfirst=True)")
                parts = raw.str.extract(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$").astype(float)
                slash = parts[0].notna() & d.notna()
                if slash.any() and (parts.loc[slash, 0] > 12).any():  # the export is day-first (dd/mm/yyyy)
                    wrong = int((d[slash].dt.month != parts.loc[slash, 1]).sum())
                    if wrong:
                        problems.append(f"{wrong:,} dd/mm/yyyy dates were read month-first (e.g. 02/08/2026 must be "
                                        "2 Aug, not 8 Feb): use pd.to_datetime(col, format='mixed', dayfirst=True)")
    if problems:
        raise ValueError("DATA CHECK failed: " + " | ".join(problems))
    print("  DATA CHECK passed")
'''
DATA_CHECK_IMPORT = f"from {GATE_MODULE} import data_check"
DATA_CHECK_SOURCE = GATE_SOURCE[GATE_SOURCE.index("def data_check"):]


def standalone_script(code: str) -> str:
    """analysis.py must re-run anywhere: inline the sandbox's data_check instead of importing it."""
    if DATA_CHECK_IMPORT in code:
        code = code.replace(DATA_CHECK_IMPORT, "# data_check: copied from the DataPilot sandbox\n" + DATA_CHECK_SOURCE, 1)
    return code


class SandboxExecutorAgent(CodeExecutorAgent):
    """CodeExecutorAgent that (1) refuses obviously unsafe code, (2) enforces the chart quality check,
    (3) tags results OK/ERROR, (4) saves the last working script as analysis.py and tracks its charts."""

    def __init__(self, *args, state: RunState, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = state

    async def execute_code_block(self, code_blocks: List[CodeBlock], cancellation_token: CancellationToken) -> CodeResult:
        st = self._state
        code_blocks = [b for b in code_blocks if b.language.lower().startswith("py")] or code_blocks
        code = "\n\n".join(b.code for b in code_blocks)
        st.executions += 1

        problems = gr.check_code(code)
        if problems:
            st.errors += 1
            st.last_status = "error"
            return CodeResult(exit_code=1, output=(
                f"{ERR_TAG}\nBlocked by sandbox policy:\n- " + "\n- ".join(problems[:10])
                + "\nOnly pandas/numpy/matplotlib on the local data file are allowed. Rewrite without these."))

        start = time.time() - 1
        (st.run_dir / f"{GATE_MODULE}.py").write_text(GATE_SOURCE, encoding="utf-8")
        gated = [CodeBlock(code=GATE_IMPORT + code, language="python")]
        result = await super().execute_code_block(gated, cancellation_token)
        out = gr.cap_output(gr.redact(result.output))

        if result.exit_code != 0:
            st.errors += 1
            st.last_status = "error"
            return CodeResult(exit_code=result.exit_code, output=f"{ERR_TAG}\n{out}")

        if st.last_status == "error":
            st.fixed_after_error += 1
        st.last_status = "ok"
        charts = sorted(p.name for p in st.run_dir.glob("*.png") if p.stat().st_mtime >= start)
        st.last_ok_code = code
        st.last_ok_output = result.output
        st.last_ok_charts = charts
        header = (
            "# analysis.py - generated by DataPilot agents and run successfully in the sandbox.\n"
            "# Re-run with: python analysis.py   (from this folder, next to the data file)\n\n"
        )
        (st.run_dir / "analysis.py").write_text(header + standalone_script(code), encoding="utf-8")
        (st.run_dir / "last_output.txt").write_text(result.output, encoding="utf-8")
        chart_note = f"\nCharts saved: {', '.join(charts)}" if charts else "\nCharts saved: none"
        return CodeResult(exit_code=0, output=f"{OK_TAG}\n{out}{chart_note}")


# ---------------------------------------------------------------- approval
def is_approval(text: str) -> bool:
    """The Reviewer approved: it said APPROVED, or it sent the full final-answer format (models sometimes
    drop the last word). Feedback messages never count."""
    t = (text or "").strip()
    if not t or re.search(r"^\W*FEEDBACK\b", t, re.M | re.I):
        return False
    if re.search(r"\bAPPROVED\b", t) and not re.search(r"\bNOT\s+APPROVED\b", t, re.I):
        return True
    return all(re.search(rf"^\W*{h}\W*$", t, re.M | re.I) for h in ("ANSWER", "SUGGESTED NEXT QUESTION"))


def clean_review(text: str) -> str:
    """If the Reviewer gave feedback, drop any approval it also wrote: feedback always wins."""
    if not re.search(r"^\W*FEEDBACK\b", text, re.M | re.I):
        return text
    m = re.search(r"^\W*ANSWER\W*$", text, re.M | re.I)
    kept = text[: m.start()] if m else text
    return re.sub(r"\bAPPROVED\b", "", kept).rstrip()


class ReviewerAgent(AssistantAgent):
    """AssistantAgent whose replies are normalised so a message is either feedback or an approval."""

    async def on_messages_stream(self, messages, cancellation_token):
        async for item in super().on_messages_stream(messages, cancellation_token):
            if isinstance(item, Response) and isinstance(item.chat_message, TextMessage):
                cleaned = clean_review(item.chat_message.content)
                if cleaned != item.chat_message.content:
                    item = Response(
                        chat_message=TextMessage(content=cleaned, source=self.name,
                                                 models_usage=item.chat_message.models_usage),
                        inner_messages=item.inner_messages)
            yield item


def _reviewer_approved(messages: Sequence) -> bool:
    return any(getattr(m, "source", "") == "Reviewer" and is_approval(str(getattr(m, "content", "")))
               for m in messages)


# ---------------------------------------------------------------- routing
def rules_selector(messages: Sequence) -> str | None:
    if not messages:
        return "Planner"
    last = messages[-1]
    src = getattr(last, "source", "")
    text = str(getattr(last, "content", ""))
    if src == "Planner":
        return "Coder"
    if src == "Coder":
        return "Executor"
    if src == "Executor":
        return "Reviewer" if text.startswith(OK_TAG) else "Coder"
    if src == "Reviewer":
        return "Coder"
    return "Planner"  # a new user question (first or follow-up)


# ---------------------------------------------------------------- build
@dataclass
class DataPilotSession:
    team: object  # SelectorGroupChat or RoundRobinGroupChat
    state: RunState
    executor: object
    model_client: object
    extra_clients: tuple = ()

    async def close(self):
        try:
            if hasattr(self.executor, "stop"):
                await self.executor.stop()
        finally:
            for c in (self.model_client, *self.extra_clients):
                if hasattr(c, "close"):
                    await c.close()


def new_run_dir(session_id: str) -> Path:
    d = SANDBOX_ROOT / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_code_executor(run_dir: Path, use_docker: bool):
    timeout = int(os.getenv("EXEC_TIMEOUT", "60"))
    if use_docker:
        from autogen_ext.code_executors.docker import DockerCommandLineCodeExecutor

        return DockerCommandLineCodeExecutor(
            image=os.getenv("DOCKER_IMAGE", "datapilot-sandbox:latest"), work_dir=run_dir, timeout=timeout
        )
    # No secrets in the child's environment, CPU/memory/file-size limits, capped output (guardrails.py).
    return gr.SafeLocalCodeExecutor(work_dir=run_dir, timeout=timeout)


async def build_session(
    model_client,
    run_dir: Path,
    data_file: Path,
    schema: str,
    max_messages: int = 12,
    routing: str = "rules",
    use_docker: bool = False,
    role_clients: dict | None = None,
) -> DataPilotSession:
    """role_clients: optional per-agent model clients, e.g. {"coder": <cheap model client>}."""
    role_clients = role_clients or {}
    max_messages = max(4, min(max_messages, gr.MAX_AGENT_MESSAGES))
    state = RunState(run_dir=run_dir, data_file=data_file, schema=schema)
    code_executor = _make_code_executor(run_dir, use_docker)
    if hasattr(code_executor, "start"):
        await code_executor.start()

    planner = AssistantAgent("Planner", model_client=role_clients.get("planner", model_client), system_message=PLANNER_PROMPT,
                             description="Writes the step-by-step analysis plan.")
    coder = AssistantAgent("Coder", model_client=role_clients.get("coder", model_client), system_message=CODER_PROMPT,
                           description="Writes and fixes the Python analysis script.")
    executor = SandboxExecutorAgent("Executor", code_executor=code_executor, sources=["Coder"],
                                    supported_languages=["python", "py"], state=state,
                                    description="Runs the Coder's script in the sandbox and reports output or errors.")
    reviewer = ReviewerAgent("Reviewer", model_client=role_clients.get("reviewer", model_client), system_message=REVIEWER_PROMPT,
                              description="Checks results and charts; approves or sends feedback.")

    termination = (TextMentionTermination("APPROVED", sources=["Reviewer"])
                   | FunctionalTermination(_reviewer_approved)
                   | MaxMessageTermination(max_messages)
                   | TimeoutTermination(gr.RUN_TIMEOUT_SECONDS))
    agents = [planner, coder, executor, reviewer]
    if routing == "round_robin":
        # Fixed turn order Planner -> Coder -> Executor -> Reviewer -> Planner ... (simplest team type)
        team = RoundRobinGroupChat(agents, termination_condition=termination)
    else:
        team = SelectorGroupChat(
            agents,
            model_client=model_client,
            termination_condition=termination,
            selector_func=rules_selector if routing == "rules" else None,
            selector_prompt=SELECTOR_PROMPT,
            allow_repeated_speaker=False,
        )
    return DataPilotSession(team=team, state=state, executor=code_executor, model_client=model_client,
                            extra_clients=tuple(c for c in role_clients.values() if c is not model_client))


def build_task(question: str, schema: str) -> str:
    return f"QUESTION:\n{question}\n\nDATASET SCHEMA:\n{gr.wrap_untrusted(schema)}"


def copy_into_run(src: Path, run_dir: Path) -> Path:
    dest = run_dir / src.name
    shutil.copyfile(src, dest)
    return dest
