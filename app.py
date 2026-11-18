"""DataPilot - Self-Correcting Data Analyst (Streamlit chat UI).

Run:  streamlit run app.py
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st
from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import BaseChatMessage, TextMessage
from autogen_core import CancellationToken

import config
import demo
import ui
from data_utils import describe_data, stage_data_file
from report import export_pdf, outputs_match, parse_final_answer, rerun_analysis
import usage_guard
from team import OK_TAG, SANDBOX_ROOT, build_session, build_task, is_approval, new_run_dir

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "sample_data" / "sales_2026.csv"
MESSY = ROOT / "sample_data" / "sales_2026_messy.xlsx"
IN_CONTAINER = os.getenv("DATAPILOT_IN_CONTAINER") == "1"
ROUTING_LABELS = {"rules": "Selector · fixed rules", "llm": "Selector · LLM picks", "round_robin": "RoundRobin"}

st.set_page_config(page_title="DataPilot", page_icon="📊", layout="wide")
ui.inject_css()
ss = st.session_state
ss.setdefault("sid", uuid.uuid4().hex[:8])
ss.setdefault("log", [])          # rendered chat items
ss.setdefault("turns", [])        # one record per question
ss.setdefault("session", None)    # DataPilotSession (AutoGen team lives here across reruns)
ss.setdefault("data_path", None)
ss.setdefault("schema", None)
if "loop" not in ss:
    ss.loop = asyncio.new_event_loop()   # one loop per browser session; model clients stay bound to it


def run_async(coro):
    return ss.loop.run_until_complete(coro)


def reset_session(keep_data=False):
    if ss.session is not None:
        try:
            run_async(ss.session.close())
        except Exception:
            pass
    ss.session = None
    ss.log, ss.turns = [], []
    if not keep_data:
        ss.data_path, ss.schema = None, None
        ss.sid = uuid.uuid4().hex[:8]


def human_says(text: str, prompt: str) -> str:
    """Pass the human's decision through AutoGen's UserProxyAgent (human-in-the-loop step)."""
    proxy = UserProxyAgent("User", description="Business owner", input_func=lambda _p: text)
    resp = run_async(proxy.on_messages([TextMessage(content=prompt, source="DataPilot")], CancellationToken()))
    return str(resp.chat_message.content)


# ---------------------------------------------------------------- sidebar
has_key = config.detect_provider() is not None
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    st.markdown(f'<span class="dp-chip" style="--c:{ui.EMERALD if has_key else ui.ROSE}">'
                f'{"● " + config.describe_roles() if has_key else "● no API key"}</span>', unsafe_allow_html=True)
    demo_mode = st.toggle("Demo mode (scripted model)", value=not has_key,
                          help="Replays a scripted Planner/Coder/Reviewer for the sample question. "
                               "The code, the error and the numbers are real.")
    if not has_key and not demo_mode:
        st.error("No API key found. Add it to datapilot/.env (see .env.example).")
    routing = st.radio("Team / who speaks next", list(ROUTING_LABELS), format_func=ROUTING_LABELS.get,
                       disabled=demo_mode or ss.session is not None,
                       help="Selector + fixed rules: errors go straight back to the Coder (default). "
                            "LLM picks: SelectorGroupChat lets the model choose. RoundRobin: fixed turn order.")
    max_msgs = st.slider("Stop after N messages", 6, 24, 12, disabled=ss.session is not None)
    if IN_CONTAINER:
        use_docker = False
        st.caption("🐳 Code runs in this app's isolated Docker container sandbox.")
    else:
        use_docker = st.toggle("Run code in Docker", value=False, disabled=ss.session is not None,
                               help="Needs Docker and the image: docker build -t datapilot-sandbox -f Dockerfile.sandbox .")
    left = usage_guard.runs_left(SANDBOX_ROOT)
    if left is not None and not demo_mode:
        st.caption(f"Live runs left today: **{left}**")
    if st.button("🔄 New session", use_container_width=True):
        reset_session()
        st.rerun()
    st.divider()
    st.markdown("**Agents**")
    st.markdown(ui.legend_html(), unsafe_allow_html=True)
    st.caption("Agents see only the column names and 5 sample rows. Every number comes from code run in the sandbox.")

ui.hero()
pipe = st.empty()


def show_pipeline(active=None, done=None):
    pipe.markdown(ui.pipeline_html(active, done), unsafe_allow_html=True)


show_pipeline(None, {"user", "Planner", "Coder", "Executor", "Reviewer", "answer"} if ss.turns else set())

# ---------------------------------------------------------------- data
if ss.data_path is None:
    c1, c2 = st.columns([3, 1])
    up = c1.file_uploader("Upload sales data (CSV or Excel, multi-sheet OK)", type=["csv", "xlsx", "xls"])
    c2.write("")
    c2.write("")
    use_sample = c2.button("✨ Use sample sales_2026.csv", use_container_width=True, type="primary")
    use_messy = c2.button("🧪 Messy real-world export", use_container_width=True, disabled=demo_mode,
                          help="Same sales as a typical shop/ERP Excel export: mixed date formats, '₹1,999' prices, "
                               "'10%' discounts, region spelling variants, blank cells, plus a targets sheet. "
                               "Watch the sandbox's data check catch the Coder's cleaning bugs and the Coder fix them.")
    if up is not None or use_sample or use_messy:
        run_dir = new_run_dir(ss.sid)
        if up is not None:
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(up.name).suffix) as tmp:
                tmp.write(up.getbuffer())
            ss.data_path = stage_data_file(tmp.name, run_dir, up.name)
            ss.data_name = up.name
        else:
            src = MESSY if use_messy else SAMPLE
            ss.data_path = stage_data_file(src, run_dir)
            ss.data_name = src.name
        ss.schema = describe_data(ss.data_path)
        st.rerun()
    st.info("Upload a file or use the sample to start. Try: *" + demo.DEMO_QUESTION + "*")
    st.stop()

data_path: Path = ss.data_path
run_dir = data_path.parent
with st.expander(f"📁 {ss.data_name} · what the agents see", expanded=False):
    if data_path.suffix == ".csv":
        st.dataframe(pd.read_csv(data_path, nrows=200), use_container_width=True, height=220)
    else:
        st.dataframe(pd.read_excel(data_path, nrows=200), use_container_width=True, height=220)
    st.code(ss.schema, language="text")


# ---------------------------------------------------------------- rendering
def render_item(item):
    src, text = item["source"], item["content"]
    with st.chat_message("user" if src in ("user", "User") else "assistant", avatar=ui.AGENT_ICON.get(src, "🤖")):
        if src in ("user", "User"):
            st.markdown(ui.agent_header(src, item.get("tag")), unsafe_allow_html=True)
            st.markdown(text)
            return
        if src == "Executor":
            ok = text.startswith(OK_TAG)
            st.markdown(ui.agent_header("Executor", "OK" if ok else "ERROR", error=not ok), unsafe_allow_html=True)
            body = text.split("\n", 1)[1] if "\n" in text else text
            st.code(body[-4000:], language="text")
        elif src == "Reviewer":
            approved = is_approval(text)
            st.markdown(ui.agent_header("Reviewer", "APPROVED" if approved else "FEEDBACK"), unsafe_allow_html=True)
            shown = text.replace("APPROVED", "").strip() if approved else text
            st.markdown(shown.replace("\n", "  \n"))
        elif src == "Coder" and "```" in text:
            intro = text.split("```", 1)[0].strip()
            tag = item.get("tag") or ("FIX" if intro.lower().startswith("fixed") else "CODE")
            st.markdown(ui.agent_header("Coder", tag), unsafe_allow_html=True)
            if intro:
                st.markdown(intro)
            with st.expander("Show code", expanded=False):
                st.markdown(text[len(text.split("```", 1)[0]):])
        else:
            st.markdown(ui.agent_header(src, "PLAN" if src == "Planner" else None), unsafe_allow_html=True)
            st.markdown(text)


def render_turn_result(i, turn):
    stats = turn["stats"]
    st.markdown(ui.stats_html([
        (turn["n_messages"], "messages", ui.VIOLET),
        (stats["runs"], "code runs", ui.SKY),
        (stats["fixed"], "errors self-fixed", ui.EMERALD),
        ("APPROVED" if turn["approved"] else "STOPPED", "reviewer", ui.AMBER if turn["approved"] else ui.ROSE),
    ]), unsafe_allow_html=True)

    if turn["approved"]:
        st.markdown(ui.answer_html(turn["answer"]), unsafe_allow_html=True)
    else:
        st.warning(f"Stopped without approval: {turn['stop_reason']}. Let the agents continue, or ask a clearer question.")

    tdir = Path(turn["dir"])
    charts = [tdir / c for c in turn["charts"] if (tdir / c).exists()]
    if charts:
        cols = st.columns(max(len(charts), 2))
        for col, c in zip(cols, charts):
            col.image(str(c), caption=c.name, use_container_width=True)

    c = st.columns(4)
    code_file = tdir / "analysis.py"
    if code_file.exists():
        c[0].download_button("⬇️ analysis.py", code_file.read_bytes(), "analysis.py", key=f"dl_code_{i}",
                             use_container_width=True)
    for j, ch in enumerate(charts[:2]):
        c[1 + j].download_button(f"⬇️ {ch.name}", ch.read_bytes(), ch.name, mime="image/png", key=f"dl_ch_{i}_{j}",
                                 use_container_width=True)
    if turn["approved"]:
        pdf = tdir / "report.pdf"
        if not pdf.exists():
            export_pdf(turn["question"], turn["answer"], charts, pdf)
        c[3].download_button("⬇️ PDF report", pdf.read_bytes(), f"datapilot_report_{i + 1}.pdf",
                             mime="application/pdf", key=f"dl_pdf_{i}", use_container_width=True)

    with st.expander("🔁 Reproducibility check: re-run analysis.py"):
        if code_file.exists() and st.button("Re-run now", key=f"rerun_{i}"):
            ok, out = rerun_analysis(run_dir, code_file)
            same = ok and outputs_match(out, turn["output"])
            (st.success if same else st.error)(
                "Same numbers as the approved run ✅" if same else "Output differs from the approved run ❌")
            st.code(out[-3000:], language="text")
    st.caption(f"Team: {ROUTING_LABELS.get(turn.get('routing'), 'demo')} · stop reason: {turn['stop_reason']}")

    is_last = i == len(ss.turns) - 1
    if turn["approved"] and is_last:
        if turn.get("human") == "approved":
            st.success("👤 You approved this answer (via UserProxyAgent).")
        else:
            h1, h2 = st.columns([1, 3])
            if h1.button("✅ Approve answer", key=f"approve_{i}", type="primary", use_container_width=True):
                reply = human_says("APPROVE", "Approve the final answer, or ask a follow-up question.")
                turn["human"] = "approved" if reply.strip().upper() == "APPROVE" else reply
                st.rerun()
            h2.caption("Human in the loop: approve the answer, or type a follow-up below. "
                       "Follow-ups continue the same agent conversation.")
        nxt = turn["answer"].get("SUGGESTED NEXT QUESTION")
        if nxt and not demo_mode and st.button(f"💡 Ask: {nxt}", key=f"next_{i}"):
            ss.pending_question = nxt
            st.rerun()
    elif not turn["approved"] and is_last and not demo_mode:
        if st.button("▶️ Let the agents continue", key=f"cont_{i}"):
            ss.pending_continue = True
            st.rerun()


for item in ss.log:
    if item.get("type") == "turn_end":
        render_turn_result(item["turn"], ss.turns[item["turn"]])
    else:
        render_item(item)


# ---------------------------------------------------------------- running the team
async def ensure_session():
    if ss.session is None:
        client = demo.demo_model_client(data_path) if demo_mode else config.make_model_client()
        roles = {} if demo_mode else {r: config.make_model_client(m) for r in ("planner", "coder", "reviewer")
                                      if (m := config.role_model(r))}
        ss.session = await build_session(client, run_dir, data_path, ss.schema, max_messages=max_msgs,
                                         routing="rules" if demo_mode else routing, use_docker=use_docker,
                                         role_clients=roles)
        ss.session_routing = "rules" if demo_mode else routing
    return ss.session


async def run_turn(question: str | None):
    sess = await ensure_session()
    first = len(ss.turns) == 0
    task = build_task(question, ss.schema) if (question and first) else question
    state = sess.state
    runs0, errs0 = state.executions, state.errors
    done: set[str] = {"user"}
    show_pipeline("Planner", done)
    status = st.status("Agents are working…", expanded=True)
    result, n = None, 0
    async for item in sess.team.run_stream(task=task):
        if isinstance(item, TaskResult):
            result = item
            break
        if not isinstance(item, BaseChatMessage) or item.source == "user":
            continue
        n += 1
        entry = {"source": item.source, "content": str(item.content)}
        if item.source == "Coder":
            prev = next((e for e in reversed(ss.log) if e.get("source") in ("Executor", "Reviewer", "Planner")), None)
            if prev and prev["source"] == "Executor" and not prev["content"].startswith(OK_TAG):
                entry["tag"] = "FIX · SELF-CORRECTION"
            elif prev and prev["source"] == "Reviewer":
                entry["tag"] = "FIX · REVIEW FEEDBACK"
        ss.log.append(entry)
        with status:
            render_item(entry)
        done.add(item.source)
        nxt = {"Planner": "Coder", "Coder": "Executor", "Reviewer": "Coder",
               "Executor": "Reviewer" if entry["content"].startswith(OK_TAG) else "Coder"}.get(item.source)
        show_pipeline(nxt, done)
        status.update(label=f"Agents are working… {item.source} just spoke, {nxt or 'next'} is up")
    last = result.messages[-1] if result and result.messages else None
    approved = bool(last is not None and last.source == "Reviewer" and is_approval(str(last.content)))
    show_pipeline("answer" if approved else None, done | ({"answer"} if approved else set()))
    status.update(label="Done ✅ Reviewer approved" if approved else "Stopped", state="complete" if approved else "error",
                  expanded=False)

    k = len(ss.turns)
    tdir = run_dir / f"turn_{k + 1}"
    tdir.mkdir(exist_ok=True)
    for f in ["analysis.py", "last_output.txt", *state.last_ok_charts]:
        if (run_dir / f).exists():
            shutil.copyfile(run_dir / f, tdir / f)
    ss.turns.append({
        "question": question or "(continued)",
        "approved": approved,
        "answer": parse_final_answer(str(last.content)) if approved else {},
        "charts": list(state.last_ok_charts),
        "output": state.last_ok_output,
        "dir": str(tdir),
        "routing": ss.session_routing,
        "stop_reason": result.stop_reason if result else "unknown",
        "n_messages": len(result.messages) if result else n,
        "stats": {"runs": state.executions - runs0,
                  "fixed": (state.errors - errs0) if state.last_status == "ok" else 0},  # errors the agents repaired
    })
    ss.log.append({"type": "turn_end", "turn": k})


pending_q = ss.pop("pending_question", None)
pending_c = ss.pop("pending_continue", False)
locked = demo_mode and len(ss.turns) > 0
placeholder = demo.DEMO_QUESTION if not ss.turns else "Ask a follow-up question…"
typed = st.chat_input("Demo mode answers the sample question once. Turn it off to ask follow-ups."
                      if locked else placeholder, disabled=locked)
question = typed or pending_q

if (question or pending_c) and not demo_mode and not usage_guard.take_run(SANDBOX_ROOT):
    st.error("Today's limit of live agent runs on this server is reached. Try demo mode, or come back tomorrow.")
elif question or pending_c:
    if question:
        if ss.turns:  # follow-up: the human's message goes through the UserProxyAgent
            question = human_says(question, "Approve the final answer, or ask a follow-up question.")
        ss.log.append({"source": "user", "content": question, "tag": "FOLLOW-UP" if ss.turns else "QUESTION"})
        render_item(ss.log[-1])
    try:
        run_async(run_turn(question))
    except Exception as e:  # show model/API errors in the UI instead of crashing
        st.error(f"Run failed: {e}")
        st.stop()
    st.rerun()
elif not ss.turns:
    if st.button(f"▶️ Try the sample question: {demo.DEMO_QUESTION}"):
        ss.pending_question = demo.DEMO_QUESTION
        st.rerun()
