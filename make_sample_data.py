"""Generate a realistic sample: sample_data/sales_2026.csv (12,480 rows) and a 2-sheet Excel workbook.

Built-in story for the demo question: Electronics revenue drops in September vs August,
mostly in the South region, driven by Earbuds X2 and Smartwatch Lite.
"""
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path(__file__).resolve().parent / "sample_data"
N_ROWS = 12_480
rng = np.random.default_rng(2026)

CATALOG = {
    "Electronics": [("Earbuds X2", 1999), ("Smartwatch Lite", 3499), ("Power Bank 20k", 1499),
                    ("BT Speaker Mini", 2499), ("USB-C Charger 33W", 899)],
    "Grocery": [("Millet Mix 1kg", 249), ("Cold Pressed Oil 1L", 389), ("Basmati Rice 5kg", 699),
                ("Masala Combo", 199), ("Green Tea 100", 299)],
    "Apparel": [("Cotton Kurta", 899), ("Denim Jeans", 1499), ("Running Tee", 599), ("Silk Saree", 3999)],
    "Home & Kitchen": [("Steel Kadai", 1299), ("Mixer Grinder", 3299), ("Bedsheet Set", 1099), ("Water Bottle", 349)],
    "Beauty": [("Herbal Shampoo", 299), ("Face Serum", 649), ("Sunscreen 50ml", 399)],
}
REGIONS = ["North", "South", "East", "West"]
REGION_W = np.array([0.27, 0.30, 0.20, 0.23])
CAT_W = {"Electronics": 0.24, "Grocery": 0.30, "Apparel": 0.18, "Home & Kitchen": 0.16, "Beauty": 0.12}
UNITS = {"Electronics": (3, 18), "Grocery": (10, 60), "Apparel": (2, 15), "Home & Kitchen": (2, 12), "Beauty": (5, 30)}
DISCOUNTS = np.array([0.0, 0.05, 0.10, 0.15, 0.20])
SEASON = {1: .9, 2: .85, 3: .95, 4: 1.0, 5: 1.0, 6: .95, 7: 1.0, 8: 1.1, 9: 1.05, 10: 1.25}


def keep_probability(month, region, product):
    """The injected September drop (rows are thinned out, so revenue falls)."""
    if month == 9 and region == "South" and product in ("Earbuds X2", "Smartwatch Lite"):
        return 0.30
    if month == 9 and region == "South" and product in ("Power Bank 20k", "BT Speaker Mini"):
        return 0.85
    if month == 9 and product in ("Earbuds X2", "Smartwatch Lite"):
        return 0.92
    return 1.0


def generate() -> pd.DataFrame:
    days = pd.date_range("2026-01-01", "2026-10-31", freq="D")
    day_w = np.array([SEASON[d.month] * (1.15 if d.dayofweek >= 5 else 1.0) for d in days])
    day_w /= day_w.sum()
    cats = list(CAT_W)
    cat_w = np.array([CAT_W[c] for c in cats])

    rows = []
    while len(rows) < N_ROWS:
        d = days[rng.choice(len(days), p=day_w)]
        region = REGIONS[rng.choice(4, p=REGION_W)]
        cat = cats[rng.choice(len(cats), p=cat_w)]
        product, price = CATALOG[cat][rng.integers(len(CATALOG[cat]))]
        if rng.random() > keep_probability(d.month, region, product):
            continue
        lo, hi = UNITS[cat]
        units = int(rng.integers(lo, hi + 1))
        disc = float(rng.choice(DISCOUNTS, p=[0.45, 0.2, 0.2, 0.1, 0.05]))
        rows.append((d.strftime("%Y-%m-%d"), region, cat, product, units, price, disc))

    df = pd.DataFrame(rows, columns=["order_date", "region", "category", "product", "units", "unit_price", "discount"])
    return df.sort_values(["order_date", "region", "category", "product"], kind="stable").reset_index(drop=True)


def make_messy(df: pd.DataFrame) -> pd.DataFrame:
    """Same sales, exported the way real shop/ERP systems do it: the classic things that break naive code.
    - headers with spaces and units ("Order Date", "Unit Price (Rs)")
    - dates in three formats (2026-08-02, 02/08/2026 day-first, 02-Aug-2026)
    - prices as text with a rupee sign and thousands separators ("₹1,999")
    - discounts as "10%" text or 0.1 numbers
    - region spelling/case/whitespace variants ("South", "south ", "SOUTH")
    - a few blank units cells
    """
    r = np.random.default_rng(7)
    m = df.copy()
    d = pd.to_datetime(m["order_date"])
    fmt = r.integers(0, 3, len(m))
    m["order_date"] = np.where(fmt == 0, d.dt.strftime("%Y-%m-%d"),
                               np.where(fmt == 1, d.dt.strftime("%d/%m/%Y"), d.dt.strftime("%d-%b-%Y")))
    m["unit_price"] = ["₹" + f"{p:,}" for p in m["unit_price"]]
    m["discount"] = [f"{x * 100:.0f}%" if r.random() < 0.5 else f"{x:.2f}" for x in m["discount"]]
    variants = {"South": ["South", "south ", "SOUTH"], "North": ["North", "north"], "East": ["East", " East"],
                "West": ["West", "WEST"]}
    m["region"] = [variants[x][r.integers(len(variants[x]))] for x in m["region"]]
    units = m["units"].astype("object")
    units[r.random(len(m)) < 0.004] = ""
    m["units"] = units
    return m.rename(columns={"order_date": "Order Date", "region": "Region", "category": "Category",
                             "product": "Product", "units": "Units", "unit_price": "Unit Price (Rs)",
                             "discount": "Discount"})


def main():
    OUT.mkdir(exist_ok=True)
    df = generate()
    df.to_csv(OUT / "sales_2026.csv", index=False)

    months = pd.to_datetime(df["order_date"]).dt.to_period("M").astype(str)
    rev = df["units"] * df["unit_price"] * (1 - df["discount"])
    targets = (rev.groupby([months, df["region"]]).sum() * 1.05).round(-3).rename("revenue_target").reset_index()
    targets.columns = ["month", "region", "revenue_target"]
    with pd.ExcelWriter(OUT / "sales_2026.xlsx") as xw:
        df.to_excel(xw, sheet_name="Sales", index=False)
        targets.to_excel(xw, sheet_name="Region_Targets", index=False)

    messy = make_messy(df)
    messy.to_csv(OUT / "sales_2026_messy.csv", index=False, encoding="utf-8-sig")
    with pd.ExcelWriter(OUT / "sales_2026_messy.xlsx") as xw:
        messy.to_excel(xw, sheet_name="Sales Export", index=False)
        targets.to_excel(xw, sheet_name="Region_Targets", index=False)

    print(f"Wrote {len(df):,} rows to {OUT / 'sales_2026.csv'} and a 2-sheet workbook sales_2026.xlsx")
    print("Wrote messy real-world versions: sales_2026_messy.csv / .xlsx")


if __name__ == "__main__":
    main()
