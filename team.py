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
from autogen_agentchat.conditions import FunctionalTermination, MaxMessageTermination, TextMentionTermination
from autogen_agentchat.teams import RoundRobinGroupChat, SelectorGroupChat
from autogen_core import CancellationToken
from autogen_core.code_executor import CodeBlock, CodeResult

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
one line: "Plan unchanged - Coder, apply the latest feedback." unless the plan itself was wrong."""

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
- Print a clear SUMMARY block at the end with the headline numbers (before, after, change, % change, shares).
- Deterministic results: no randomness, sort outputs.
- Never use input(), the network, subprocess, or delete/modify files other than the PNGs you create.
When the Executor reports an error: say in ONE line what was wrong, then resend the FULL corrected script.
When the Reviewer gives feedback: apply it and resend the FULL script. Never send partial snippets.
Never reply without a ```python block: you are only called when code must be written or fixed."""

REVIEWER_PROMPT = """You are the Reviewer. You check the latest Executor output (and the Coder's script)
before the user sees anything. Check:
1. The last Executor run says STATUS: OK.
2. The printed results answer EVERY part of the user's question. For comparisons: the TOTAL before and after
   values, the absolute change and the % change must be printed. For "what caused it": each contributor's share
   (%) of the TOTAL change must be printed. A breakdown number must never be presented as the total.
3. Numbers are sensible and consistent (parts add up to the total, % changes match the before/after values).
4. Every chart in the script has a title, x and y axis labels, and units in the y label. No pie charts.
Feedback is ONLY for blocking problems: a failed run, a part of the question with no printed number,
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

The last line of an approval MUST be the single word APPROVED."""

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


BLOCKED_PATTERNS = [
    r"\bimport\s+subprocess\b", r"\bfrom\s+subprocess\b", r"\bos\.system\s*\(", r"\bos\.popen\s*\(",
    r"\bimport\s+socket\b", r"\bimport\s+requests\b", r"\burllib\.request\b", r"\bshutil\.rmtree\s*\(",
    r"\bos\.remove\s*\(", r"\bos\.unlink\s*\(", r"\bos\.rmdir\s*\(", r"\b__import__\s*\(",
    r"(?<![.\w])eval\s*\(", r"(?<![.\w])exec\s*\(", r"(?<![.\w])input\s*\(",
    r"\.\./", r"\.\.\\", r"['\"][A-Za-z]:[\\/]",
]


class SandboxExecutorAgent(CodeExecutorAgent):
    """CodeExecutorAgent that (1) refuses obviously unsafe code, (2) tags results OK/ERROR,
    (3) saves the last working script as analysis.py and tracks which charts it produced."""

    def __init__(self, *args, state: RunState, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = state

    async def execute_code_block(self, code_blocks: List[CodeBlock], cancellation_token: CancellationToken) -> CodeResult:
        st = self._state
        code_blocks = [b for b in code_blocks if b.language.lower().startswith("py")] or code_blocks
        code = "\n\n".join(b.code for b in code_blocks)
        st.executions += 1

        for pat in BLOCKED_PATTERNS:
            if re.search(pat, code):
                st.errors += 1
                st.last_status = "error"
                return CodeResult(exit_code=1, output=(
                    f"{ERR_TAG}\nBlocked by sandbox policy: code matches forbidden pattern {pat!r}. "
                    "Only pandas/matplotlib on the local data file are allowed."))

        start = time.time() - 1
        result = await super().execute_code_block(code_blocks, cancellation_token)
        out = result.output
        if len(out) > 6000:
            out = out[:3000] + "\n...[output truncated]...\n" + out[-3000:]

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
        (st.run_dir / "analysis.py").write_text(header + code, encoding="utf-8")
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

    async def close(self):
        try:
            if hasattr(self.executor, "stop"):
                await self.executor.stop()
        finally:
            if hasattr(self.model_client, "close"):
                await self.model_client.close()


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
    from autogen_ext.code_executors.local import LocalCommandLineCodeExecutor

    return LocalCommandLineCodeExecutor(work_dir=run_dir, timeout=timeout)


async def build_session(
    model_client,
    run_dir: Path,
    data_file: Path,
    schema: str,
    max_messages: int = 12,
    routing: str = "rules",
    use_docker: bool = False,
) -> DataPilotSession:
    state = RunState(run_dir=run_dir, data_file=data_file, schema=schema)
    code_executor = _make_code_executor(run_dir, use_docker)
    if hasattr(code_executor, "start"):
        await code_executor.start()

    planner = AssistantAgent("Planner", model_client=model_client, system_message=PLANNER_PROMPT,
                             description="Writes the step-by-step analysis plan.")
    coder = AssistantAgent("Coder", model_client=model_client, system_message=CODER_PROMPT,
                           description="Writes and fixes the Python analysis script.")
    executor = SandboxExecutorAgent("Executor", code_executor=code_executor, sources=["Coder"],
                                    supported_languages=["python", "py"], state=state,
                                    description="Runs the Coder's script in the sandbox and reports output or errors.")
    reviewer = ReviewerAgent("Reviewer", model_client=model_client, system_message=REVIEWER_PROMPT,
                              description="Checks results and charts; approves or sends feedback.")

    termination = (TextMentionTermination("APPROVED", sources=["Reviewer"])
                   | FunctionalTermination(_reviewer_approved)
                   | MaxMessageTermination(max_messages))
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
    return DataPilotSession(team=team, state=state, executor=code_executor, model_client=model_client)


def build_task(question: str, schema: str) -> str:
    return f"QUESTION:\n{question}\n\nDATASET SCHEMA:\n{schema}"


def copy_into_run(src: Path, run_dir: Path) -> Path:
    dest = run_dir / src.name
    shutil.copyfile(src, dest)
    return dest
