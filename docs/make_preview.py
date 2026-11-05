"""Render a static preview of the main report pages from the warehouse views.

This is a quick visual check of the numbers the Power BI report reads, generated
straight from the exported views. Run it after the ETL:
    python docs/make_preview.py
"""
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXP = ROOT / "exports"

pnl = pd.read_csv(EXP / "vw_pnl_summary_monthly.csv").sort_values("fiscal_month_no")
cash = pd.read_csv(EXP / "vw_cash_position_monthly.csv").sort_values("fiscal_month_no")
ar = pd.read_csv(EXP / "vw_ar_aging.csv").groupby(["aging_bucket_sort", "aging_bucket"]).open_amount.sum()
ap = pd.read_csv(EXP / "vw_ap_aging.csv").groupby(["aging_bucket_sort", "aging_bucket"]).open_amount.sum()

lakh = 1e5
ink, accent, muted, warn = "#1f2a44", "#2f6fdf", "#9aa5b8", "#d9822b"
plt.rcParams.update({"font.size": 9, "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": muted, "axes.titleweight": "bold", "axes.titlecolor": ink})
fig, axes = plt.subplots(2, 2, figsize=(13, 7.5))
fig.suptitle("Deccan Consumer Distributors (fictional) - FY2025-26 finance overview, INR lakh",
             fontsize=12, fontweight="bold", color=ink, x=0.01, ha="left")

ax = axes[0, 0]
x = range(len(pnl))
ax.bar(x, pnl.revenue / lakh, color=accent, label="Revenue")
ax.bar(x, pnl.net_profit / lakh, color=warn, width=0.45, label="Net profit")
ax.set_xticks(list(x), [m[:3] for m in pnl.month_label])
ax.axhline(0, color=muted, lw=0.8)
ax.set_title("Revenue and net profit by month")
ax.legend(frameon=False)

ax = axes[0, 1]
ax.plot(range(len(cash)), cash.closing_balance / lakh, color=accent, marker="o")
ax.set_xticks(range(len(cash)), [m[:3] for m in cash.month_label])
ax.set_title("Closing bank balance")

for ax, data, title in [(axes[1, 0], ar, "Receivables aging (open amount)"),
                        (axes[1, 1], ap, "Payables aging (open amount)")]:
    labels = [b for _, b in data.index]
    colors = [accent if b == "Current" else warn for b in labels]
    ax.barh(labels[::-1], (data.values / lakh)[::-1], color=colors[::-1])
    for i, v in enumerate((data.values / lakh)[::-1]):
        ax.text(v, i, f" {v:,.1f}", va="center", color=ink)
    ax.set_title(title)

totals = (f"Revenue {pnl.revenue.sum()/lakh:,.0f}  |  Gross margin "
          f"{(pnl.revenue.sum()-pnl.cost_of_sales.sum())/pnl.revenue.sum():.1%}  |  Net profit "
          f"{pnl.net_profit.sum()/lakh:,.1f}  |  Closing cash {cash.closing_balance.iloc[-1]/lakh:,.1f}")
fig.text(0.01, 0.925, totals, color=muted)
fig.tight_layout(rect=(0, 0, 1, 0.91))
fig.savefig(ROOT / "docs" / "preview_dashboard.png", dpi=150)
print("wrote docs/preview_dashboard.png")
