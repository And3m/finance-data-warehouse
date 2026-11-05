"""End-to-end tests: generate source data, run the ETL, and check the accounting holds."""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import sqlglot

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "etl"))
import run_etl  # noqa: E402


def _generate(out, *extra):
    subprocess.run([sys.executable, str(ROOT / "generator" / "generate_source_data.py"),
                    "--out", str(out), *extra], check=True, capture_output=True)


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("clean")
    _generate(tmp / "src")
    db = tmp / "dw.sqlite"
    rc = run_etl.main(["--source", str(tmp / "src"), "--db-url", f"sqlite:///{db}",
                       "--exports", str(tmp / "exports")])
    assert rc == 0
    return sqlite3.connect(db), tmp


def q(cx, sql):
    return cx.execute(sql).fetchall()


def test_schema_and_views_parse_as_tsql():
    for f in ["01_schema_sqlserver.sql", "02_views_sqlserver.sql"]:
        raw = (ROOT / "sql" / f).read_text()
        for chunk in raw.split("\nGO"):
            if "CREATE" in chunk:
                assert sqlglot.parse(chunk, read="tsql")


def test_all_dq_checks_pass(clean_db):
    cx, _ = clean_db
    assert q(cx, "SELECT COUNT(*) FROM dq_result WHERE passed = 0 AND severity = 'ERROR'")[0][0] == 0
    assert q(cx, "SELECT status FROM etl_run")[0][0] == "SUCCESS"


def test_trial_balance_is_zero(clean_db):
    cx, _ = clean_db
    assert abs(q(cx, "SELECT SUM(balance) FROM vw_trial_balance")[0][0]) < 0.01


def test_balance_sheet_equation(clean_db):
    """Assets = Liabilities + Equity + current-year profit."""
    cx, _ = clean_db
    rows = dict(q(cx, "SELECT account_type, SUM(balance) FROM vw_trial_balance GROUP BY account_type"))
    assets = rows["Asset"]
    liab_equity = -(rows["Liability"] + rows["Equity"])
    profit = -(rows["Revenue"] + rows["Expense"])
    assert abs(assets - (liab_equity + profit)) < 0.01


def test_pnl_views_agree(clean_db):
    cx, _ = clean_db
    summary = q(cx, "SELECT SUM(net_profit) FROM vw_pnl_summary_monthly")[0][0]
    detail = q(cx, "SELECT SUM(CASE WHEN pnl_group = 'Revenue' THEN pnl_amount ELSE -pnl_amount END) "
                   "FROM vw_pnl_monthly")[0][0]
    assert abs(summary - detail) < 0.01


def test_cash_view_matches_bank_account(clean_db):
    cx, _ = clean_db
    closing = q(cx, "SELECT closing_balance FROM vw_cash_position_monthly ORDER BY fiscal_month_no DESC")[0][0]
    bank = q(cx, "SELECT balance FROM vw_trial_balance WHERE account_code = '1000'")[0][0]
    assert abs(closing - bank) < 0.01


def test_aging_buckets_cover_all_open_items(clean_db):
    cx, _ = clean_db
    assert abs(q(cx, "SELECT SUM(open_amount) FROM vw_ar_aging")[0][0]
               - q(cx, "SELECT SUM(open_amount) FROM fact_ar_invoice")[0][0]) < 0.01


def test_exports_written(clean_db):
    _, tmp = clean_db
    for v in run_etl.VIEWS + run_etl.TABLES_IN_LOAD_ORDER:
        assert (tmp / "exports" / f"{v}.csv").exists()


def test_seeded_defects_stop_the_load(tmp_path):
    _generate(tmp_path / "src", "--inject-issues")
    db = tmp_path / "dw.sqlite"
    rc = run_etl.main(["--source", str(tmp_path / "src"), "--db-url", f"sqlite:///{db}",
                       "--exports", str(tmp_path / "exports")])
    assert rc == 1
    cx = sqlite3.connect(db)
    failed = {r[0] for r in q(cx, "SELECT check_name FROM dq_result WHERE passed = 0")}
    assert {"journals_balanced", "gl_accounts_exist_in_chart",
            "ar_invoices_unique_invoice_no", "ap_invoices_not_over_settled"} <= failed
    assert q(cx, "SELECT COUNT(*) FROM fact_gl")[0][0] == 0
