"""Extract, validate, transform, and load the finance source extracts into the warehouse.

Steps
  1. Extract   read the source CSV extracts (GL, AP, AR, master data)
  2. Validate  staging-level data quality checks; ERROR results stop the load
  3. Transform build dimensions with surrogate keys and fact tables, including
               AR/AP open balances and aging as of the snapshot date
  4. Load      create the schema from sql/01_schema_sqlserver.sql and insert rows
  5. Reconcile warehouse-level checks (trial balance, subledger vs control accounts)
  6. Publish   create the reporting views and export them as CSV for Power BI

Target database
  Default is a local SQLite file (warehouse/finance_dw.sqlite), no setup needed.
  For SQL Server set DW_URL to a SQLAlchemy URL, for example:
    mssql+pyodbc://user:pass@localhost/FinanceDW?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes

Usage
  python etl/run_etl.py [--source data/source] [--as-of 2026-03-31]
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import sqlglot
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_SQL = ROOT / "sql" / "01_schema_sqlserver.sql"
VIEWS_SQL = ROOT / "sql" / "02_views_sqlserver.sql"
TABLES_IN_LOAD_ORDER = ["dim_date", "dim_account", "dim_cost_center", "dim_customer", "dim_vendor",
                        "fact_gl", "fact_ar_invoice", "fact_ap_invoice"]
VIEWS = ["vw_trial_balance", "vw_pnl_monthly", "vw_pnl_summary_monthly", "vw_ar_aging",
         "vw_ap_aging", "vw_cash_position_monthly"]
AGING_BUCKETS = [(0, "Current", 1), (30, "1-30", 2), (60, "31-60", 3), (90, "61-90", 4)]
TOLERANCE = 0.01  # rupees


# ---------------------------------------------------------------- extract

def extract(source: Path) -> dict[str, pd.DataFrame]:
    str_cols = {"account_code": str, "cost_center_code": str}
    names = ["chart_of_accounts", "cost_centers", "vendors", "customers", "gl_journal_lines",
             "ap_invoices", "ap_payments", "ar_invoices", "ar_receipts"]
    return {n: pd.read_csv(source / f"{n}.csv", dtype=str_cols, keep_default_na=False) for n in names}


# ---------------------------------------------------------------- data quality

class DQ:
    def __init__(self):
        self.results = []

    def check(self, name, stage, failing: int, detail="", severity="ERROR"):
        self.results.append({"check_name": name, "stage": stage, "severity": severity,
                             "passed": int(failing == 0), "failing_rows": int(failing), "detail": detail[:400]})

    @property
    def errors(self):
        return [r for r in self.results if r["severity"] == "ERROR" and not r["passed"]]

    def report(self):
        for r in self.results:
            mark = "PASS" if r["passed"] else r["severity"]
            print(f"  [{mark:<7}] {r['stage']:<9} {r['check_name']:<42} {r['detail']}")


def staging_checks(src, dq: DQ, fy_start: date, fy_end: date):
    gl = src["gl_journal_lines"]
    jb = gl.groupby("journal_id")[["debit", "credit"]].sum()
    unbalanced = jb[(jb.debit - jb.credit).abs() > TOLERANCE]
    dq.check("journals_balanced", "staging", len(unbalanced),
             f"{len(unbalanced)} of {len(jb)} journals have debits not equal to credits")

    bad_acct = gl[~gl.account_code.isin(src["chart_of_accounts"].account_code)]
    dq.check("gl_accounts_exist_in_chart", "staging", len(bad_acct),
             f"unknown codes: {sorted(bad_acct.account_code.unique())[:5]}" if len(bad_acct) else "")

    bad_cc = gl[~gl.cost_center_code.isin(src["cost_centers"].cost_center_code)]
    dq.check("gl_cost_centers_exist", "staging", len(bad_cc))

    dates = pd.to_datetime(gl.posting_date).dt.date
    out = gl[(dates < fy_start) | (dates > fy_end)]
    dq.check("posting_dates_within_fiscal_year", "staging", len(out))

    neg = gl[(gl.debit < 0) | (gl.credit < 0) | ((gl.debit > 0) & (gl.credit > 0))]
    dq.check("gl_lines_single_sided_non_negative", "staging", len(neg))

    for sub, ref, key, amt in [("ar_invoices", "ar_receipts", "receipts", "amount"),
                               ("ap_invoices", "ap_payments", "payments", "amount")]:
        inv = src[sub]
        dup = inv[inv.invoice_no.duplicated(keep=False)]
        dq.check(f"{sub}_unique_invoice_no", "staging", dup.invoice_no.nunique(),
                 f"duplicates: {sorted(dup.invoice_no.unique())[:5]}" if len(dup) else "")
        orphans = src[ref][~src[ref].invoice_no.isin(inv.invoice_no)]
        dq.check(f"{ref}_match_an_invoice", "staging", len(orphans))
        settled = src[ref].groupby("invoice_no")[amt].sum()
        totals = inv.drop_duplicates("invoice_no").set_index("invoice_no").total_amount
        over = (settled - totals.reindex(settled.index)) > TOLERANCE
        dq.check(f"{sub}_not_over_settled", "staging", int(over.sum()),
                 f"{int(over.sum())} invoices with {key} above invoice total" if over.any() else "")

    cust_cc = src["customers"][~src["customers"].cost_center_code.isin(src["cost_centers"].cost_center_code)]
    dq.check("customers_have_valid_cost_center", "staging", len(cust_cc))


def warehouse_checks(wh, dq: DQ):
    gl, acct = wh["fact_gl"], wh["dim_account"]
    tb = round(gl.amount.sum(), 2)
    dq.check("trial_balance_nets_to_zero", "warehouse", int(abs(tb) > TOLERANCE), f"net = {tb + 0.0:,.2f}")

    bal = gl.merge(acct, on="account_key").groupby("account_code").amount.sum()
    for code, fact, label in [("1100", "fact_ar_invoice", "AR"), ("2000", "fact_ap_invoice", "AP")]:
        control = round(bal.get(code, 0.0) * (1 if code == "1100" else -1), 2)
        sub = round(wh[fact].open_amount.sum(), 2)
        diff = round(control - sub, 2)
        dq.check(f"{label.lower()}_subledger_ties_to_control_{code}", "warehouse",
                 int(abs(diff) > TOLERANCE), f"GL {control:,.2f} vs subledger {sub:,.2f} (diff {diff:,.2f})")

    for fact in ["fact_ar_invoice", "fact_ap_invoice"]:
        neg = (wh[fact].open_amount < -TOLERANCE).sum()
        dq.check(f"{fact}_no_negative_open_balance", "warehouse", int(neg))

    cash = bal.get("1000", 0.0)
    dq.check("bank_balance_not_overdrawn", "warehouse", int(cash < 0), f"closing bank {cash:,.2f}",
             severity="WARNING")


# ---------------------------------------------------------------- transform

def fiscal_fields(d: pd.Timestamp):
    fy_start_year = d.year if d.month >= 4 else d.year - 1
    fm = (d.month - 4) % 12 + 1
    return f"FY{fy_start_year}-{str(fy_start_year + 1)[-2:]}", f"Q{(fm - 1) // 3 + 1}", fm


def build_dim_date(all_dates: pd.Series) -> pd.DataFrame:
    rng = pd.date_range(all_dates.min(), all_dates.max(), freq="D")
    rows = []
    for d in rng:
        fy, fq, fm = fiscal_fields(d)
        rows.append({"date_key": int(d.strftime("%Y%m%d")), "full_date": d.date(),
                     "calendar_year": d.year, "calendar_month": d.month, "month_name": d.strftime("%B"),
                     "month_label": d.strftime("%b-%Y"), "fiscal_year": fy, "fiscal_quarter": fq,
                     "fiscal_month_no": fm, "is_month_end": int(d.is_month_end)})
    return pd.DataFrame(rows)


def date_key(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y%m%d").astype(int)


def with_key(df: pd.DataFrame, key: str) -> pd.DataFrame:
    df = df.reset_index(drop=True).copy()
    df.insert(0, key, range(1, len(df) + 1))
    return df


def aging(days: int):
    for limit, label, sort in AGING_BUCKETS:
        if days <= limit:
            return label, sort
    return "90+", 5


def build_open_items(inv, settle, settle_date_col, as_of: date, party_map, party_col, settled_name):
    s = settle[pd.to_datetime(settle[settle_date_col]).dt.date <= as_of]
    settled = s.groupby("invoice_no").amount.sum()
    f = inv[pd.to_datetime(inv.invoice_date).dt.date <= as_of].copy()
    f[settled_name] = f.invoice_no.map(settled).fillna(0.0).round(2)
    f["open_amount"] = (f.total_amount - f[settled_name]).round(2)
    due = pd.to_datetime(f.due_date).dt.date
    f["days_past_due"] = [max((as_of - d).days, 0) if o > TOLERANCE else 0 for d, o in zip(due, f.open_amount)]
    buckets = [aging(d) for d in f.days_past_due]
    f["aging_bucket"] = [b[0] for b in buckets]
    f["aging_bucket_sort"] = [b[1] for b in buckets]
    f["status"] = ["Paid" if o <= TOLERANCE else ("Partially Paid" if p > 0 else "Open")
                   for o, p in zip(f.open_amount, f[settled_name])]
    f[party_col.replace("_id", "_key")] = f[party_col].map(party_map)
    f["invoice_date_key"] = date_key(f.invoice_date)
    f["due_date_key"] = date_key(f.due_date)
    cols = ["invoice_no", party_col.replace("_id", "_key"), "invoice_date_key", "due_date_key", "net_amount",
            "gst_amount", "total_amount", settled_name, "open_amount", "days_past_due", "aging_bucket",
            "aging_bucket_sort", "status"]
    return f[cols]


def transform(src, as_of: date) -> dict[str, pd.DataFrame]:
    gl = src["gl_journal_lines"]
    gl = gl[pd.to_datetime(gl.posting_date).dt.date <= as_of]
    all_dates = pd.concat([pd.to_datetime(x) for x in [
        gl.posting_date, src["ar_invoices"].due_date, src["ap_invoices"].due_date]])

    dim_account = with_key(src["chart_of_accounts"], "account_key")
    dim_account["pnl_group"] = dim_account.pnl_group.replace("", None)
    dim_cc = with_key(src["cost_centers"], "cost_center_key")
    dim_customer = with_key(src["customers"], "customer_key")
    dim_vendor = with_key(src["vendors"], "vendor_key")

    acct_map = dict(zip(dim_account.account_code, dim_account.account_key))
    cc_map = dict(zip(dim_cc.cost_center_code, dim_cc.cost_center_key))
    fact_gl = with_key(gl, "gl_line_key")
    fact_gl["date_key"] = date_key(fact_gl.posting_date)
    fact_gl["account_key"] = fact_gl.account_code.map(acct_map)
    fact_gl["cost_center_key"] = fact_gl.cost_center_code.map(cc_map)
    fact_gl["amount"] = (fact_gl.debit - fact_gl.credit).round(2)
    fact_gl = fact_gl[["gl_line_key", "journal_id", "line_no", "date_key", "account_key", "cost_center_key",
                       "debit", "credit", "amount", "source", "doc_ref"]]

    return {
        "dim_date": build_dim_date(all_dates),
        "dim_account": dim_account,
        "dim_cost_center": dim_cc,
        "dim_customer": dim_customer,
        "dim_vendor": dim_vendor,
        "fact_gl": fact_gl,
        "fact_ar_invoice": build_open_items(src["ar_invoices"], src["ar_receipts"], "receipt_date", as_of,
                                            dict(zip(dim_customer.customer_id, dim_customer.customer_key)),
                                            "customer_id", "received_amount"),
        "fact_ap_invoice": build_open_items(src["ap_invoices"], src["ap_payments"], "payment_date", as_of,
                                            dict(zip(dim_vendor.vendor_id, dim_vendor.vendor_key)),
                                            "vendor_id", "paid_amount"),
    }


# ---------------------------------------------------------------- load and publish

def sql_statements(path: Path, dialect: str) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    chunks = [c for c in raw.split("\nGO") if "CREATE" in c] if "\nGO" in raw else [raw]
    stmts = []
    for chunk in chunks:
        parsed = sqlglot.parse(chunk, read="tsql")
        stmts += [p.sql(dialect=dialect) for p in parsed if p is not None]
    return stmts


def reset_and_create(engine, dialect):
    with engine.begin() as cx:
        for v in VIEWS:
            cx.execute(text(f"DROP VIEW IF EXISTS {v}"))
        for t in reversed(TABLES_IN_LOAD_ORDER + ["etl_run", "dq_result"]):
            cx.execute(text(f"DROP TABLE IF EXISTS {t}"))
        for stmt in sql_statements(SCHEMA_SQL, dialect):
            cx.execute(text(stmt))


def create_views(engine, dialect):
    with engine.begin() as cx:
        for stmt in sql_statements(VIEWS_SQL, dialect):
            cx.execute(text(stmt))


def append(engine, name, df):
    df.to_sql(name, engine, if_exists="append", index=False, chunksize=1000)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default=str(ROOT / "data" / "source"))
    p.add_argument("--as-of", default="2026-03-31", help="snapshot date for AR/AP balances and aging")
    p.add_argument("--fy-start", default="2025-04-01")
    p.add_argument("--exports", default=str(ROOT / "exports"))
    p.add_argument("--db-url", default=os.environ.get("DW_URL"))
    a = p.parse_args(argv)

    as_of = date.fromisoformat(a.as_of)
    fy_start = date.fromisoformat(a.fy_start)
    fy_end = date(fy_start.year + 1, 3, 31)
    db_url = a.db_url or f"sqlite:///{ROOT / 'warehouse' / 'finance_dw.sqlite'}"
    if db_url.startswith("sqlite:///"):
        Path(db_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(db_url)
    dialect = "tsql" if engine.dialect.name == "mssql" else engine.dialect.name
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print(f"Finance DW ETL  target={engine.dialect.name}  as_of={as_of}")
    src = extract(Path(a.source))
    print(f"  extracted {len(src['gl_journal_lines']):,} GL lines, {len(src['ar_invoices']):,} AR and "
          f"{len(src['ap_invoices']):,} AP invoices")

    dq = DQ()
    staging_checks(src, dq, fy_start, fy_end)
    reset_and_create(engine, dialect)
    run = {"run_id": 1, "started_at": started, "snapshot_date": as_of}

    if dq.errors:
        dq.report()
        append(engine, "dq_result", pd.DataFrame(dq.results).assign(run_id=1))
        append(engine, "etl_run", pd.DataFrame([{**run, "finished_at": None, "status": "FAILED",
                                                 "gl_rows_loaded": 0}]))
        print(f"\nLoad stopped: {len(dq.errors)} data quality error(s) in staging. Fix the source and re-run.")
        return 1

    wh = transform(src, as_of)
    for t in TABLES_IN_LOAD_ORDER:
        append(engine, t, wh[t])
    warehouse_checks(wh, dq)
    create_views(engine, dialect)
    status = "FAILED" if dq.errors else "SUCCESS"
    append(engine, "dq_result", pd.DataFrame(dq.results).assign(run_id=1))
    append(engine, "etl_run", pd.DataFrame([{**run, "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                             "status": status, "gl_rows_loaded": len(wh["fact_gl"])}]))
    dq.report()

    out = Path(a.exports)
    out.mkdir(parents=True, exist_ok=True)
    with engine.connect() as cx:
        for obj in TABLES_IN_LOAD_ORDER + VIEWS:
            pd.read_sql(text(f"SELECT * FROM {obj}"), cx).to_csv(out / f"{obj}.csv", index=False)
    print(f"\n{status}: loaded {len(wh['fact_gl']):,} GL lines; tables and views exported to {out}")
    return 0 if status == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
