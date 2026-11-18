"""Offline checks for the Done checklist (no API key needed). Run: python test_offline.py

1. The agents fix a code error on their own (real KeyError traceback -> Coder rewrites -> OK).
2. Code runs only inside the sandbox folder.
3. Run stops on APPROVED, and a never-approving reviewer stops at 12 messages.
4. Re-running the saved analysis.py gives exactly the same output.
5. Every chart has a title, axis labels and units.
6. A follow-up question continues the same team/session.
"""
import asyncio
import shutil
import sys
import uuid
from pathlib import Path

from autogen_agentchat.base import TaskResult
from autogen_ext.models.replay import ReplayChatCompletionClient

import demo
from data_utils import describe_data, stage_data_file
from report import export_pdf, outputs_match, parse_final_answer, rerun_analysis
from team import ERR_TAG, OK_TAG, build_session, build_task, new_run_dir

ROOT = Path(__file__).resolve().parent
SAMPLE = ROOT / "sample_data" / "sales_2026.csv"


async def run(session, task):
    msgs, result = [], None
    async for item in session.team.run_stream(task=task):
        if isinstance(item, TaskResult):
            result = item
        else:
            msgs.append(item)
    return msgs, result


async def test_self_correction_and_approval():
    run_dir = new_run_dir("test-" + uuid.uuid4().hex[:6])
    data = stage_data_file(SAMPLE, run_dir)
    schema = describe_data(data)
    session = await build_session(demo.demo_model_client(data), run_dir, data, schema)
    try:
        msgs, result = await run(session, build_task(demo.DEMO_QUESTION, schema))
        chat = [m for m in result.messages]
        speakers = [m.source for m in chat]
        print("speakers:", " -> ".join(speakers))
        print("stop reason:", result.stop_reason)

        exec_msgs = [str(m.content) for m in chat if m.source == "Executor"]
        assert exec_msgs[0].startswith(ERR_TAG) and "KeyError" in exec_msgs[0] and "revenue" in exec_msgs[0], exec_msgs[0][:300]
        assert exec_msgs[1].startswith(ERR_TAG) and "Chart quality check failed" in exec_msgs[1], exec_msgs[1][-400:]
        assert exec_msgs[2].startswith(OK_TAG), "Coder's second fix should run OK"
        assert session.state.fixed_after_error >= 1 and session.state.errors == 2
        print("[1] self-correction OK: KeyError -> fixed, chart check failure -> fixed")

        files = {p.name for p in run_dir.iterdir()}
        assert {"analysis.py", "category_trend.png", "region_breakdown.png"} <= files, files
        assert not any((ROOT / f).exists() for f in ("category_trend.png", "region_breakdown.png"))
        print("[2] artifacts written only inside", run_dir)

        assert "APPROVED" in str(chat[-1].content) and chat[-1].source == "Reviewer"
        assert len(chat) <= 12
        print(f"[3] stopped on APPROVED after {len(chat)} messages")

        ok, out = rerun_analysis(run_dir)
        assert ok and outputs_match(out, session.state.last_ok_output), out[-500:]
        print("[4] analysis.py re-run gives identical output")

        code = (run_dir / "analysis.py").read_text()
        assert code.count("set_title") == 2 and code.count("set_xlabel") == 2 and code.count("Rs lakh") == 2
        print("[5] both charts have title, axis labels and Rs units")

        ans = parse_final_answer(str(chat[-1].content))
        print("\nFINAL ANSWER\n", ans["ANSWER"], "\n", ans["WHERE IT CAME FROM"])
        pdf = export_pdf(demo.DEMO_QUESTION, ans, [run_dir / "category_trend.png", run_dir / "region_breakdown.png"],
                         run_dir / "report.pdf")
        assert pdf.stat().st_size > 10_000
        print("[stretch] PDF report:", pdf)

        # follow-up continues the SAME team: give the replay client one more round of answers
        session.model_client.chat_completions.extend([  # noqa: SLF001 (test only)
            "1) Average discount on Earbuds X2 in South for Aug vs Sep.",
            "```python\nimport pandas as pd\ndf = pd.read_csv('data.csv')\ndf['m'] = df.order_date.str[:7]\n"
            "s = df[(df['product']=='Earbuds X2') & (df.region=='South') & df.m.isin(['2026-08','2026-09'])]\n"
            "print(s.groupby('m')['discount'].mean().round(4).to_string())\n```",
            "ANSWER\nDiscounts were roughly unchanged.\nWHERE IT CAME FROM\nSee printed averages.\nFILES\nanalysis.py\n"
            "SUGGESTED NEXT QUESTION\nDid units sold fall instead?\nAPPROVED",
        ])
        _, result2 = await run(session, "Did discounts on Earbuds X2 change between August and September?")
        sp2 = [m.source for m in result2.messages]
        assert sp2[0] == "user" and sp2[1] == "Planner" and "APPROVED" in str(result2.messages[-1].content)
        history = await session.team.save_state()
        assert history, "team state should hold both turns"
        print("[6] follow-up continued the same session:", " -> ".join(sp2))
    finally:
        await session.close()


async def test_max_messages_stop():
    run_dir = new_run_dir("test-max-" + uuid.uuid4().hex[:6])
    data = stage_data_file(SAMPLE, run_dir)
    schema = describe_data(data)
    code = "```python\nprint('hello')\n```"
    replies = ["plan"] + [code, "FEEDBACK: try again"] * 10
    session = await build_session(ReplayChatCompletionClient(replies), run_dir, data, schema, max_messages=12)
    try:
        _, result = await run(session, build_task("loop forever?", schema))
        assert len(result.messages) == 12, len(result.messages)
        print(f"[3b] never-approving reviewer -> stopped at {len(result.messages)} messages ({result.stop_reason})")
    finally:
        await session.close()
    shutil.rmtree(run_dir, ignore_errors=True)


async def test_sandbox_policy():
    run_dir = new_run_dir("test-pol-" + uuid.uuid4().hex[:6])
    data = stage_data_file(SAMPLE, run_dir)
    bad = "```python\nimport subprocess\nsubprocess.run('dir', shell=True)\n```"
    session = await build_session(ReplayChatCompletionClient(["plan", bad, "x"]), run_dir, data, "", max_messages=4)
    try:
        _, result = await run(session, "x")
        ex = [str(m.content) for m in result.messages if m.source == "Executor"][0]
        assert ex.startswith(ERR_TAG) and "Blocked by sandbox policy" in ex
        print("[2b] unsafe code (subprocess) blocked by sandbox policy")
    finally:
        await session.close()
    shutil.rmtree(run_dir, ignore_errors=True)


async def main():
    await test_self_correction_and_approval()
    await test_max_messages_stop()
    await test_sandbox_policy()
    print("\nALL OFFLINE CHECKS PASSED")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
