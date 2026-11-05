"""Generate realistic, balanced source-system extracts for a fictional distributor.

The company, "Deccan Consumer Distributors Pvt Ltd", is invented. It buys
packaged consumer goods from suppliers and sells to retailers across four
South Indian cities for fiscal year 2025-26 (1 Apr 2025 to 31 Mar 2026).

Every business event posts a balanced double-entry journal, exactly as an ERP
would, and the AP and AR subledgers are produced alongside the general ledger
so the warehouse can reconcile them against the control accounts.

Usage:
    python generator/generate_source_data.py                  # clean data
    python generator/generate_source_data.py --inject-issues  # seeded DQ defects
"""
from __future__ import annotations

import argparse
import csv
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

FY_START = date(2025, 4, 1)
FY_END = date(2026, 3, 31)
GST_RATE = 0.18
COGS_RATIO = 0.72  # cost of goods as a share of net sales value

CHART_OF_ACCOUNTS = [
    # code, name, type, statement, normal balance, P&L group
    ("1000", "Cash at Bank", "Asset", "BS", "Debit", None),
    ("1100", "Accounts Receivable", "Asset", "BS", "Debit", None),
    ("1200", "Inventory", "Asset", "BS", "Debit", None),
    ("1300", "Input GST Credit", "Asset", "BS", "Debit", None),
    ("2000", "Accounts Payable", "Liability", "BS", "Credit", None),
    ("2100", "Output GST Payable", "Liability", "BS", "Credit", None),
    ("3000", "Share Capital", "Equity", "BS", "Credit", None),
    ("4000", "Sales Revenue", "Revenue", "PL", "Credit", "Revenue"),
    ("5000", "Cost of Goods Sold", "Expense", "PL", "Debit", "Cost of Sales"),
    ("6000", "Salaries and Wages", "Expense", "PL", "Debit", "Operating Expenses"),
    ("6100", "Rent", "Expense", "PL", "Debit", "Operating Expenses"),
    ("6200", "Freight Outward", "Expense", "PL", "Debit", "Operating Expenses"),
    ("6300", "Marketing and Trade Promotions", "Expense", "PL", "Debit", "Operating Expenses"),
    ("6400", "Utilities", "Expense", "PL", "Debit", "Operating Expenses"),
    ("6500", "Bank Charges", "Expense", "PL", "Debit", "Finance Costs"),
]

COST_CENTERS = [
    ("HO", "Head Office", "Bengaluru"),
    ("BLR", "Sales Bengaluru", "Bengaluru"),
    ("HYD", "Sales Hyderabad", "Hyderabad"),
    ("CHN", "Sales Chennai", "Chennai"),
    ("CBE", "Sales Coimbatore", "Coimbatore"),
]

VENDORS = [
    # id, name, category, payment terms (days), typical delay in paying them
    ("V001", "Sunrise Foods Ltd", "Goods", 45, 5),
    ("V002", "Kaveri Personal Care Pvt Ltd", "Goods", 30, 8),
    ("V003", "Godavari Home Products Ltd", "Goods", 45, 3),
    ("V004", "Malabar Beverages Pvt Ltd", "Goods", 30, 12),
    ("V005", "Swift Logistics", "Freight", 15, 2),
    ("V006", "BrightAds Media", "Marketing", 30, 10),
    ("V007", "City Power and Water", "Utilities", 15, 0),
]
GOODS_VENDORS = [v for v in VENDORS if v[2] == "Goods"]

CUSTOMER_PREFIXES = ["Sri Lakshmi", "Annapurna", "Green Valley", "Royal", "Metro",
                     "Sai Balaji", "Om Shakti", "New India", "Daily Needs", "Ganesh"]
CUSTOMER_SUFFIXES = ["Supermarket", "Stores", "Traders", "Mart", "Retail"]


@dataclass
class Ledger:
    lines: list = field(default_factory=list)
    _seq: int = 0

    def post(self, posting_date, source, doc_ref, narration, entries):
        """entries: list of (account_code, cost_center, debit, credit)."""
        entries = [(a, cc, round(d, 2), round(c, 2)) for a, cc, d, c in entries]
        total_dr = round(sum(e[2] for e in entries), 2)
        total_cr = round(sum(e[3] for e in entries), 2)
        diff = round(total_dr - total_cr, 2)
        if diff:  # absorb rounding pennies on the last line so journals balance
            a, cc, d, c = entries[-1]
            entries[-1] = (a, cc, d, round(c + diff, 2)) if c else (a, cc, round(d - diff, 2), c)
        self._seq += 1
        jid = f"JE{posting_date:%y%m}-{self._seq:05d}"
        for n, (a, cc, d, c) in enumerate(entries, 1):
            self.lines.append({
                "journal_id": jid, "line_no": n, "posting_date": posting_date.isoformat(),
                "account_code": a, "cost_center_code": cc, "debit": f"{d:.2f}",
                "credit": f"{c:.2f}", "source": source, "doc_ref": doc_ref, "narration": narration,
            })
        return jid


def month_starts():
    d = FY_START
    while d <= FY_END:
        yield d
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


def rand_day(rng, start, end):
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build(seed=42):
    rng = random.Random(seed)
    gl = Ledger()
    ap_inv, ap_pay, ar_inv, ar_rec = [], [], [], []
    gst_by_month = defaultdict(lambda: {"in": 0.0, "out": 0.0})

    customers = []
    for i in range(40):
        cc = COST_CENTERS[1 + i % 4]
        name = f"{rng.choice(CUSTOMER_PREFIXES)} {rng.choice(CUSTOMER_SUFFIXES)} {cc[2]}"
        terms = rng.choice([15, 30, 30, 45])
        reliability = rng.choice(["Prompt", "Prompt", "Average", "Average", "Slow"])
        customers.append((f"C{i+1:03d}", name, cc[2], cc[0], terms, reliability))
    delay_by_class = {"Prompt": (0, 5), "Average": (3, 25), "Slow": (20, 80)}

    gl.post(FY_START, "OPENING", "CAPITAL", "Share capital introduced",
            [("1000", "HO", 15_000_000, 0), ("3000", "HO", 0, 15_000_000)])

    def ap_bill(vendor, bill_date, net, expense_acct, cc, gst=True):
        vid, vname, _, terms, delay = vendor
        inv_no = f"AP-{len(ap_inv)+1:05d}"
        tax = round(net * GST_RATE, 2) if gst else 0.0
        total = round(net + tax, 2)
        due = bill_date + timedelta(days=terms)
        ap_inv.append({"invoice_no": inv_no, "vendor_id": vid, "invoice_date": bill_date.isoformat(),
                       "due_date": due.isoformat(), "net_amount": f"{net:.2f}",
                       "gst_amount": f"{tax:.2f}", "total_amount": f"{total:.2f}"})
        entries = [(expense_acct, cc, net, 0)]
        if tax:
            entries.append(("1300", "HO", tax, 0))
            gst_by_month[(bill_date.year, bill_date.month)]["in"] += tax
        entries.append(("2000", "HO", 0, total))
        gl.post(bill_date, "AP", inv_no, f"Vendor bill {vname}", entries)
        pay_date = due + timedelta(days=rng.randint(-3, delay + 5))
        if pay_date <= FY_END:
            pay_no = f"PAY-{len(ap_pay)+1:05d}"
            ap_pay.append({"payment_no": pay_no, "invoice_no": inv_no,
                           "payment_date": pay_date.isoformat(), "amount": f"{total:.2f}"})
            gl.post(pay_date, "BANK", pay_no, f"Payment to {vname}",
                    [("2000", "HO", total, 0), ("1000", "HO", 0, total)])

    for m in month_starts():
        m_end = date(m.year + (m.month == 12), m.month % 12 + 1, 1) - timedelta(days=1)
        season = 1.25 if m.month in (9, 10, 11) else (0.9 if m.month in (6, 7) else 1.0)

        # Purchases of stock: 14-17 vendor bills a month
        for _ in range(rng.randint(14, 17)):
            ap_bill(rng.choice(GOODS_VENDORS), rand_day(rng, m, m_end),
                    rng.uniform(210_000, 380_000) * season, "1200", "HO")

        # Sales invoices: 55-75 a month, with cost of goods relieved from inventory
        for _ in range(int(rng.randint(55, 75) * season)):
            cid, cname, _, cc, terms, rel = rng.choice(customers)
            inv_date = rand_day(rng, m, m_end)
            net = round(rng.uniform(40_000, 160_000), 2)
            tax = round(net * GST_RATE, 2)
            total = round(net + tax, 2)
            inv_no = f"AR-{len(ar_inv)+1:05d}"
            due = inv_date + timedelta(days=terms)
            ar_inv.append({"invoice_no": inv_no, "customer_id": cid, "invoice_date": inv_date.isoformat(),
                           "due_date": due.isoformat(), "net_amount": f"{net:.2f}",
                           "gst_amount": f"{tax:.2f}", "total_amount": f"{total:.2f}"})
            gst_by_month[(inv_date.year, inv_date.month)]["out"] += tax
            cogs = round(net * COGS_RATIO * rng.uniform(0.95, 1.05), 2)
            gl.post(inv_date, "AR", inv_no, f"Sales invoice {cname}",
                    [("1100", cc, total, 0), ("4000", cc, 0, net), ("2100", "HO", 0, tax),
                     ("5000", cc, cogs, 0), ("1200", "HO", 0, cogs)])
            lo, hi = delay_by_class[rel]
            rec_date = due + timedelta(days=rng.randint(lo - 5, hi))
            if rec_date <= FY_END:
                # Roughly one receipt in twenty is a short payment, leaving a balance open
                paid = total if rng.random() > 0.05 else round(total * rng.uniform(0.5, 0.9), 2)
                rec_no = f"REC-{len(ar_rec)+1:05d}"
                ar_rec.append({"receipt_no": rec_no, "invoice_no": inv_no,
                               "receipt_date": rec_date.isoformat(), "amount": f"{paid:.2f}"})
                gl.post(rec_date, "BANK", rec_no, f"Receipt from {cname}",
                        [("1000", "HO", paid, 0), ("1100", cc, 0, paid)])

        # Operating expenses
        for cc in COST_CENTERS:
            sal = 250_000 if cc[0] == "HO" else rng.uniform(140_000, 180_000)
            gl.post(m_end, "BANK", f"SAL-{m:%Y%m}-{cc[0]}", f"Salaries {cc[1]}",
                    [("6000", cc[0], sal, 0), ("1000", "HO", 0, sal)])
            rent = 120_000 if cc[0] == "HO" else 60_000
            gl.post(m + timedelta(days=4), "BANK", f"RENT-{m:%Y%m}-{cc[0]}", f"Rent {cc[1]}",
                    [("6100", cc[0], rent, 0), ("1000", "HO", 0, rent)])
        for cc in COST_CENTERS[1:]:
            ap_bill(VENDORS[4], rand_day(rng, m, m_end), rng.uniform(30_000, 55_000) * season, "6200", cc[0])
        ap_bill(VENDORS[5], m + timedelta(days=9), rng.uniform(80_000, 180_000) * season,
                "6300", rng.choice(COST_CENTERS[1:])[0])
        ap_bill(VENDORS[6], m + timedelta(days=6), rng.uniform(55_000, 75_000), "6400", "HO")
        charges = rng.uniform(4_000, 9_000)
        gl.post(m_end, "BANK", f"BCHG-{m:%Y%m}", "Bank charges",
                [("6500", "HO", charges, 0), ("1000", "HO", 0, charges)])

    # Monthly GST settlement on the 20th of the following month: output tax is
    # set off against input credit and any net liability is paid from the bank.
    credit_carry = 0.0
    for (y, mth) in sorted(gst_by_month):
        due = date(y + (mth == 12), mth % 12 + 1, 20)
        if due > FY_END:
            break
        out_tax = round(gst_by_month[(y, mth)]["out"], 2)
        avail_in = round(gst_by_month[(y, mth)]["in"] + credit_carry, 2)
        set_off = min(out_tax, avail_in)
        cash = round(out_tax - set_off, 2)
        credit_carry = round(avail_in - set_off, 2)
        entries = [("2100", "HO", out_tax, 0), ("1300", "HO", 0, set_off)]
        if cash:
            entries.append(("1000", "HO", 0, cash))
        gl.post(due, "GST", f"GSTR3B-{y}{mth:02d}", f"GST settlement {y}-{mth:02d}", entries)

    gl.lines.sort(key=lambda r: (r["posting_date"], r["journal_id"], r["line_no"]))
    return {
        "chart_of_accounts": [dict(zip(["account_code", "account_name", "account_type", "statement",
                                        "normal_balance", "pnl_group"], a)) for a in CHART_OF_ACCOUNTS],
        "cost_centers": [dict(zip(["cost_center_code", "cost_center_name", "city"], c)) for c in COST_CENTERS],
        "vendors": [dict(zip(["vendor_id", "vendor_name", "category", "payment_terms_days"], v[:4]))
                    for v in VENDORS],
        "customers": [dict(zip(["customer_id", "customer_name", "city", "cost_center_code",
                                "credit_terms_days"], c[:5])) for c in customers],
        "gl_journal_lines": gl.lines,
        "ap_invoices": ap_inv, "ap_payments": ap_pay,
        "ar_invoices": ar_inv, "ar_receipts": ar_rec,
    }


def inject_issues(tables, seed=7):
    """Seed defects that the warehouse data quality checks are expected to catch."""
    rng = random.Random(seed)
    gl = tables["gl_journal_lines"]
    victim = rng.choice([r for r in gl if r["source"] == "AR" and float(r["credit"]) > 0])
    victim["credit"] = f"{float(victim['credit']) + 1000:.2f}"          # unbalanced journal
    gl.append({**gl[10], "line_no": 99, "account_code": "9999"})        # unknown account
    tables["ar_invoices"].append(dict(tables["ar_invoices"][5]))        # duplicate invoice number
    pay = tables["ap_payments"][3]
    pay["amount"] = f"{float(pay['amount']) * 1.5:.2f}"                 # overpayment
    return tables


def write(tables, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in tables.items():
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/source", help="output folder for CSV extracts")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--inject-issues", action="store_true", help="seed data quality defects")
    a = p.parse_args()
    tables = build(a.seed)
    if a.inject_issues:
        tables = inject_issues(tables)
    write(tables, Path(a.out))
    for k, v in tables.items():
        print(f"{k:<20} {len(v):>6} rows")


if __name__ == "__main__":
    main()
