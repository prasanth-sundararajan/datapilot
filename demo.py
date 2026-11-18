"""Offline demo mode: a scripted model (AutoGen ReplayChatCompletionClient) for the sample question.

The scripted Coder deliberately makes the classic mistake (KeyError: 'revenue'), then fixes it,
and the Reviewer asks for missing axis labels once. Everything else is real: the code really runs in
the sandbox, the error is a real traceback, and the numbers in the final answer are computed from the data.
Used by the tests and by the "Demo mode" toggle in the app when no API key is configured.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from autogen_ext.models.replay import ReplayChatCompletionClient

DEMO_QUESTION = ("Which category had the biggest revenue drop in September vs August, "
                 "and which region caused most of it?")

PLAN = """1) Parse order_date and compute revenue = units * unit_price * (1 - discount).
2) Add month = order_date as YYYY-MM; keep 2026-08 and 2026-09.
3) Revenue by category for both months; change and % change; pick the biggest drop.
4) For that category, split the change by region and compute each region's share of the drop.
5) In the top region, find the products that explain most of the drop.
6) Charts: category_trend.png (monthly revenue by category) and region_breakdown.png (Aug vs Sep by region)."""

_BODY = '''import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

AUG, SEP = "2026-08", "2026-09"
{load_line}
df["order_date"] = pd.to_datetime(df["order_date"])
df["month"] = df["order_date"].dt.to_period("M").astype(str)
{revenue_line}
two = df[df["month"].isin([AUG, SEP])]
cat = two.pivot_table(index="category", columns="month", values="revenue", aggfunc="sum").fillna(0)
cat["change"] = cat[SEP] - cat[AUG]
cat["pct"] = cat["change"] / cat[AUG] * 100
cat = cat.sort_values("change")
worst = cat.index[0]
print("Revenue by category, Aug vs Sep 2026:")
for name, r in cat.iterrows():
    print(f"  {{name}}: Aug Rs {{r[AUG]:,.0f}} ({{r[AUG]/1e5:.1f}} L) | Sep Rs {{r[SEP]:,.0f}} ({{r[SEP]/1e5:.1f}} L) | change {{r['change']/1e5:+.1f}} L ({{r['pct']:+.1f}}%)")
print(f"BIGGEST DROP: {{worst}}, {{cat.loc[worst, 'change']/1e5:+.1f}} L ({{cat.loc[worst, 'pct']:+.1f}}%)")

sub = two[two["category"] == worst]
reg = sub.pivot_table(index="region", columns="month", values="revenue", aggfunc="sum").fillna(0)
reg["change"] = reg[SEP] - reg[AUG]
reg = reg.sort_values("change")
total_drop = cat.loc[worst, "change"]
print(f"{{worst}} change by region:")
for name, r in reg.iterrows():
    print(f"  {{name}}: {{r['change']/1e5:+.1f}} L, share of drop {{r['change']/total_drop*100:.0f}}%")
top = reg.index[0]
prod = sub[sub["region"] == top].pivot_table(index="product", columns="month", values="revenue", aggfunc="sum").fillna(0)
prod["change"] = prod[SEP] - prod[AUG]
prod = prod.sort_values("change")
print(f"Products in {{top}} (change):")
for name, r in prod.iterrows():
    print(f"  {{name}}: {{r['change']/1e5:+.1f}} L")

trend = df.pivot_table(index="month", columns="category", values="revenue", aggfunc="sum").fillna(0) / 1e5
ax = trend.plot(figsize=(9, 5), marker="o")
ax.set_title("Monthly revenue by category, 2026")
ax.set_xlabel("Month")
ax.set_ylabel("Revenue (Rs lakh)")
plt.tight_layout(); plt.savefig("category_trend.png", dpi=120); plt.close()

ax = (reg[[AUG, SEP]] / 1e5).plot(kind="bar", figsize=(8, 5))
ax.set_title(f"{{worst}} revenue by region: Aug vs Sep 2026")
{labels}plt.tight_layout(); plt.savefig("region_breakdown.png", dpi=120); plt.close()
print("Saved charts: category_trend.png, region_breakdown.png")
'''

REVENUE = 'df["revenue"] = df["units"] * df["unit_price"] * (1 - df["discount"])'
LABELS = 'ax.set_xlabel("Region")\nax.set_ylabel("Revenue (Rs lakh)")\n'
BUGGY_REVENUE = ('cat_check = df.groupby("category")["revenue"].sum()  # revenue not created yet\n' + REVENUE)


def _sales_sheet(path: Path) -> str | None:
    """For Excel: the first sheet that has an order_date column."""
    sheets = pd.read_excel(path, sheet_name=None, nrows=1)
    return next((n for n, d in sheets.items() if "order_date" in d.columns), next(iter(sheets)))


def load_line(path: Path) -> str:
    if path.suffix.lower() == ".csv":
        return f'df = pd.read_csv("{path.name}")'
    return f'df = pd.read_excel("{path.name}", sheet_name="{_sales_sheet(path)}")'


def load_df(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path, sheet_name=_sales_sheet(path))


def scripts(path: Path) -> tuple[str, str, str]:
    """(buggy, no_labels, final) versions of the demo analysis script for this data file."""
    ll = load_line(path)
    return (_BODY.format(load_line=ll, revenue_line=BUGGY_REVENUE, labels=""),
            _BODY.format(load_line=ll, revenue_line=REVENUE, labels=""),
            _BODY.format(load_line=ll, revenue_line=REVENUE, labels=LABELS))


def _numbers(data_csv: Path) -> dict:
    df = load_df(data_csv)
    df["month"] = pd.to_datetime(df["order_date"]).dt.to_period("M").astype(str)
    df["revenue"] = df["units"] * df["unit_price"] * (1 - df["discount"])
    two = df[df["month"].isin(["2026-08", "2026-09"])]
    cat = two.pivot_table(index="category", columns="month", values="revenue", aggfunc="sum").fillna(0)
    cat["change"] = cat["2026-09"] - cat["2026-08"]
    worst = cat["change"].idxmin()
    sub = two[two["category"] == worst]
    reg = sub.pivot_table(index="region", columns="month", values="revenue", aggfunc="sum").fillna(0)
    reg["change"] = reg["2026-09"] - reg["2026-08"]
    top = reg["change"].idxmin()
    prod = sub[sub["region"] == top].pivot_table(index="product", columns="month", values="revenue", aggfunc="sum").fillna(0)
    prod["change"] = prod["2026-09"] - prod["2026-08"]
    p2 = list(prod["change"].sort_values().index[:2])
    w = cat.loc[worst]
    return dict(worst=worst, aug=w["2026-08"] / 1e5, sep=w["2026-09"] / 1e5, drop=-w["change"] / 1e5,
                pct=-w["change"] / w["2026-08"] * 100, top=top, top_drop=-reg.loc[top, "change"] / 1e5,
                share=reg.loc[top, "change"] / w["change"] * 100, p1=p2[0], p2=p2[1] if len(p2) > 1 else p2[0])


def demo_responses(data_csv: Path) -> list[str]:
    n = _numbers(data_csv)
    BUGGY, NO_LABELS, FINAL = scripts(data_csv)
    approval = f"""ANSWER
{n['worst']} had the biggest drop: revenue fell from Rs {n['aug']:.1f} L in August to Rs {n['sep']:.1f} L in September (down {n['pct']:.1f}%).
WHERE IT CAME FROM
{n['top']} region caused Rs {n['top_drop']:.1f} L of the Rs {n['drop']:.1f} L drop ({n['share']:.0f}%). Two products explain most of it: {n['p1']} and {n['p2']}.
FILES
category_trend.png | region_breakdown.png | analysis.py
SUGGESTED NEXT QUESTION
Did discounts on {n['p1']} change between August and September in the {n['top']} region?
APPROVED"""
    return [
        PLAN,
        "Here is the analysis script.\n```python\n" + BUGGY + "```",
        "Fixed: the revenue column is now created before it is used for grouping.\n```python\n" + NO_LABELS + "```",
        "FEEDBACK: region_breakdown.png has no axis labels. Add an x label (Region) and a y label with rupee units "
        "(Revenue (Rs lakh)). The numbers look consistent.",
        "Labels added to region_breakdown.png.\n```python\n" + FINAL + "```",
        approval,
    ]


def demo_model_client(data_csv: Path) -> ReplayChatCompletionClient:
    return ReplayChatCompletionClient(demo_responses(data_csv))
