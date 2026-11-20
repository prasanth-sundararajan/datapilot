"""Load an uploaded CSV / Excel file into the sandbox folder and describe its schema for the agents."""
from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd

import guardrails as gr


def stage_data_file(src_path: str | Path, run_dir: Path, original_name: str | None = None) -> Path:
    """Copy the data file into the sandbox run folder. Returns the staged path."""
    src_path = Path(src_path)
    name = original_name or src_path.name
    gr.check_upload(name, src_path.stat().st_size)  # type + size limits (raises GuardrailError)
    ext = Path(name).suffix.lower()
    dest = run_dir / f"data{ext}"
    shutil.copyfile(src_path, dest)
    return dest


def _describe_frame(df: pd.DataFrame, label: str) -> str:
    """Column names and cell values are untrusted: cleaned and truncated before the LLM sees them."""
    gr.check_table_shape(len(df), len(df.columns), label)
    lines = [f"{label}: {len(df):,} rows x {len(df.columns)} columns", "Columns:"]
    for col in df.columns:
        s = df[col]
        info = f"  - {gr.clean_cell(col, 120)} ({s.dtype})"
        if pd.api.types.is_numeric_dtype(s):
            info += f" min={s.min()} max={s.max()}"
        else:
            parsed = None
            if s.dtype == object and any(k in str(col).lower() for k in ("date", "time", "month", "day")):
                parsed = pd.to_datetime(s, errors="coerce")
            if parsed is not None and parsed.notna().mean() > 0.9:
                info += f" date range {parsed.min().date()} to {parsed.max().date()} (stored as text, parse with pd.to_datetime)"
            else:
                uniq = s.dropna().unique()
                sample = ", ".join(gr.clean_cell(u, 30) for u in uniq[:8])
                info += f" {len(uniq)} distinct, e.g. {sample}"
        lines.append(info)
    lines.append("First 5 rows (CSV):")
    head = df.head(5).rename(columns=lambda c: gr.clean_cell(c, 120)).map(lambda v: v if pd.isna(v) else gr.clean_cell(v))
    lines.append(head.to_csv(index=False).strip())
    return "\n".join(lines)


def describe_data(path: Path) -> str:
    """Schema + small sample. The LLM sees only this; all numbers must come from code."""
    ext = path.suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(path)
        body = _describe_frame(df, "Table")
        how = f"Load it with: df = pd.read_csv('{path.name}')"
    else:
        sheets = pd.read_excel(path, sheet_name=None)
        if len(sheets) > gr.MAX_SHEETS:
            raise gr.GuardrailError(f"The workbook has {len(sheets)} sheets; the limit is {gr.MAX_SHEETS}.")
        sheets = {gr.clean_cell(n, 40): df for n, df in sheets.items()}
        parts = [_describe_frame(df, f"Sheet '{name}'") for name, df in sheets.items()]
        body = "\n\n".join(parts)
        names = ", ".join(repr(n) for n in sheets)
        how = (f"Excel workbook with sheets [{names}]. Load a sheet with: "
               f"pd.read_excel('{path.name}', sheet_name='<name>')")
    return f"Data file: {path.name} (in the current working folder)\n{how}\n\n{body}"
