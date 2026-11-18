"""DataPilot in the terminal, with AutoGen's UserProxyAgent as the human approval step.

    python cli.py sample_data/sales_2026.csv "Which category dropped the most in September vs August, and which region caused it?"
    python cli.py sample_data/sales_2026.csv --demo        # scripted model, no API key needed

After the Reviewer approves, the UserProxyAgent asks you to type `approve`, or a follow-up question
(which continues the same agent conversation).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from autogen_agentchat.agents import UserProxyAgent
from autogen_agentchat.base import TaskResult
from autogen_agentchat.messages import TextMessage
from autogen_core import CancellationToken

import config
import demo
from data_utils import describe_data, stage_data_file
from report import export_pdf, parse_final_answer
from team import build_session, build_task, is_approval, new_run_dir


async def stream(team, task):
    result = None
    async for item in team.run_stream(task=task):
        if isinstance(item, TaskResult):
            result = item
        elif getattr(item, "source", "user") != "user":
            text = str(item.content)
            print(f"\n----- {item.source} -----\n{text if len(text) < 2500 else text[:2500] + ' …'}")
    return result


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("question", nargs="?", default=demo.DEMO_QUESTION)
    ap.add_argument("--demo", action="store_true", help="scripted model, no API key")
    ap.add_argument("--routing", choices=["rules", "llm", "round_robin"], default="rules")
    ap.add_argument("--max-messages", type=int, default=12)
    ap.add_argument("--docker", action="store_true")
    a = ap.parse_args()

    run_dir = new_run_dir("cli-" + uuid.uuid4().hex[:6])
    data = stage_data_file(a.data, run_dir)
    schema = describe_data(data)
    client = demo.demo_model_client(data) if a.demo else config.make_model_client()
    sess = await build_session(client, run_dir, data, schema, a.max_messages, a.routing, a.docker)
    human = UserProxyAgent("User", description="The business owner who approves the final answer.")

    question, task = a.question, build_task(a.question, schema)
    try:
        while True:
            result = await stream(sess.team, task)
            last = result.messages[-1]
            approved = last.source == "Reviewer" and is_approval(str(last.content))
            print(f"\n===== stopped: {result.stop_reason} | sandbox: {run_dir}")
            if approved:
                ans = parse_final_answer(str(last.content))
                charts = [run_dir / c for c in sess.state.last_ok_charts]
                export_pdf(question, ans, charts, run_dir / "report.pdf")
                print("Files:", ", ".join(sess.state.last_ok_charts + ["analysis.py", "report.pdf"]))
            prompt = ("Type 'approve' to accept, or ask a follow-up question" if approved
                      else "Not approved yet. Type 'continue', a clearer question, or 'quit'")
            reply = await human.on_messages([TextMessage(content=prompt, source="DataPilot")], CancellationToken())
            text = str(reply.chat_message.content).strip()
            if text.lower() in {"approve", "approved", "yes", "y", "ok", "quit", "q", "exit", ""}:
                print("Done. Human approved." if approved and text.lower() not in {"quit", "q", "exit"} else "Bye.")
                break
            if text.lower() == "continue":
                task = None
            else:
                question, task = text, text
    finally:
        await sess.close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
