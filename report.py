"""Helpers: parse the Reviewer's final answer, export a one-page PDF, re-run analysis.py for reproducibility."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from fpdf import FPDF

SECTIONS = ["ANSWER", "WHERE IT CAME FROM", "FILES", "SUGGESTED NEXT QUESTION"]


def parse_final_answer(text: str) -> dict:
    """Split the Reviewer's approval message into its sections."""
    text = text.replace("APPROVED", "").strip()
    pattern = r"^\s*\**\s*(" + "|".join(re.escape(s) for s in SECTIONS) + r")\s*\**\s*:?\s*$"
    parts, current = {}, None
    for line in text.splitlines():
        m = re.match(pattern, line, flags=re.IGNORECASE)
        if m:
            current = m.group(1).upper()
            parts[current] = []
        elif current:
            parts[current].append(line)
    out = {k: "\n".join(v).strip() for k, v in parts.items()}
    if not out:
        out = {"ANSWER": text}
    return out


def _latin1(s: str) -> str:
    repl = {"₹": "Rs ", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "•": "-", "→": "->", "…": "..."}
    for a, b in repl.items():
        s = s.replace(a, b)
    s = s.replace("**", "")
    return s.encode("latin-1", "replace").decode("latin-1")


def export_pdf(question: str, answer: dict, charts: list[Path], out_path: Path) -> Path:
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 9, "DataPilot report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 10)
    pdf.multi_cell(0, 5, _latin1("Question: " + question))
    pdf.ln(2)
    for sec in ["ANSWER", "WHERE IT CAME FROM", "SUGGESTED NEXT QUESTION"]:
        if answer.get(sec):
            pdf.set_font("Helvetica", "B", 11)
            pdf.cell(0, 6, sec.title(), new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 5, _latin1(answer[sec]))
            pdf.ln(1)
    charts = [c for c in charts if c.exists()][:2]
    if charts:
        y = pdf.get_y() + 3
        w = (pdf.w - 30) / len(charts) if len(charts) > 1 else 120
        h_left = pdf.h - y - 15
        for i, c in enumerate(charts):
            pdf.image(str(c), x=15 + i * (w + 0), y=y, w=w - 3, h=min(h_left, (w - 3) * 0.75), keep_aspect_ratio=True)
    pdf.set_y(-12)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 5, "Numbers computed by analysis.py in the DataPilot sandbox; re-run it to reproduce them.")
    pdf.output(str(out_path))
    return out_path


def rerun_analysis(run_dir: Path, script: str | Path = "analysis.py", timeout: int = 120) -> tuple[bool, str]:
    """Run a saved analysis script again inside the sandbox folder. Returns (exit_ok, stdout+stderr)."""
    proc = subprocess.run([sys.executable, str(script)], cwd=run_dir, capture_output=True, text=True, timeout=timeout)
    return proc.returncode == 0, (proc.stdout + proc.stderr)


def outputs_match(a: str | None, b: str | None) -> bool:
    norm = lambda s: "\n".join(l.rstrip() for l in (s or "").strip().splitlines())
    return norm(a) == norm(b)
