/* =====================================================================
   Finance Data Warehouse - star schema (Microsoft SQL Server / T-SQL)

   Grain:
     fact_gl          one row per general ledger journal line
     fact_ar_invoice  one row per customer invoice, as of the snapshot date
     fact_ap_invoice  one row per vendor bill, as of the snapshot date

   The ETL (etl/run_etl.py) creates these same tables on SQL Server or on
   SQLite for a zero-setup local run. tests/test_schema.py keeps this file
   and the ETL table definitions in sync.
   ===================================================================== */

/* ---------- Dimensions ---------- */

CREATE TABLE dim_date (
    date_key          INT          NOT NULL PRIMARY KEY,   -- yyyymmdd
    full_date         DATE         NOT NULL,
    calendar_year     INT          NOT NULL,
    calendar_month    INT          NOT NULL,
    month_name        VARCHAR(10)  NOT NULL,
    month_label       VARCHAR(8)   NOT NULL,               -- e.g. Apr-2025
    fiscal_year       VARCHAR(9)   NOT NULL,               -- e.g. FY2025-26 (April to March)
    fiscal_quarter    VARCHAR(2)   NOT NULL,               -- Q1 = Apr-Jun
    fiscal_month_no   INT          NOT NULL,               -- 1 = April
    is_month_end      INT          NOT NULL
);

CREATE TABLE dim_account (
    account_key       INT          NOT NULL PRIMARY KEY,
    account_code      VARCHAR(10)  NOT NULL UNIQUE,
    account_name      VARCHAR(100) NOT NULL,
    account_type      VARCHAR(20)  NOT NULL,               -- Asset, Liability, Equity, Revenue, Expense
    statement         VARCHAR(2)   NOT NULL,               -- BS or PL
    normal_balance    VARCHAR(6)   NOT NULL,               -- Debit or Credit
    pnl_group         VARCHAR(30)  NULL
);

CREATE TABLE dim_cost_center (
    cost_center_key   INT          NOT NULL PRIMARY KEY,
    cost_center_code  VARCHAR(10)  NOT NULL UNIQUE,
    cost_center_name  VARCHAR(100) NOT NULL,
    city              VARCHAR(50)  NOT NULL
);

CREATE TABLE dim_customer (
    customer_key      INT          NOT NULL PRIMARY KEY,
    customer_id       VARCHAR(10)  NOT NULL UNIQUE,
    customer_name     VARCHAR(150) NOT NULL,
    city              VARCHAR(50)  NOT NULL,
    cost_center_code  VARCHAR(10)  NOT NULL,
    credit_terms_days INT          NOT NULL
);

CREATE TABLE dim_vendor (
    vendor_key         INT          NOT NULL PRIMARY KEY,
    vendor_id          VARCHAR(10)  NOT NULL UNIQUE,
    vendor_name        VARCHAR(150) NOT NULL,
    category           VARCHAR(30)  NOT NULL,
    payment_terms_days INT          NOT NULL
);

/* ---------- Facts ---------- */

CREATE TABLE fact_gl (
    gl_line_key       INT            NOT NULL PRIMARY KEY,
    journal_id        VARCHAR(20)    NOT NULL,
    line_no           INT            NOT NULL,
    date_key          INT            NOT NULL REFERENCES dim_date (date_key),
    account_key       INT            NOT NULL REFERENCES dim_account (account_key),
    cost_center_key   INT            NOT NULL REFERENCES dim_cost_center (cost_center_key),
    debit             DECIMAL(18, 2) NOT NULL,
    credit            DECIMAL(18, 2) NOT NULL,
    amount            DECIMAL(18, 2) NOT NULL,             -- debit minus credit
    source            VARCHAR(10)    NOT NULL,             -- AP, AR, BANK, GST, OPENING
    doc_ref           VARCHAR(30)    NOT NULL
);

CREATE TABLE fact_ar_invoice (
    invoice_no        VARCHAR(20)    NOT NULL PRIMARY KEY,
    customer_key      INT            NOT NULL REFERENCES dim_customer (customer_key),
    invoice_date_key  INT            NOT NULL REFERENCES dim_date (date_key),
    due_date_key      INT            NOT NULL REFERENCES dim_date (date_key),
    net_amount        DECIMAL(18, 2) NOT NULL,
    gst_amount        DECIMAL(18, 2) NOT NULL,
    total_amount      DECIMAL(18, 2) NOT NULL,
    received_amount   DECIMAL(18, 2) NOT NULL,
    open_amount       DECIMAL(18, 2) NOT NULL,
    days_past_due     INT            NOT NULL,
    aging_bucket      VARCHAR(10)    NOT NULL,             -- Current, 1-30, 31-60, 61-90, 90+
    aging_bucket_sort INT            NOT NULL,
    status            VARCHAR(15)    NOT NULL              -- Paid, Partially Paid, Open
);

CREATE TABLE fact_ap_invoice (
    invoice_no        VARCHAR(20)    NOT NULL PRIMARY KEY,
    vendor_key        INT            NOT NULL REFERENCES dim_vendor (vendor_key),
    invoice_date_key  INT            NOT NULL REFERENCES dim_date (date_key),
    due_date_key      INT            NOT NULL REFERENCES dim_date (date_key),
    net_amount        DECIMAL(18, 2) NOT NULL,
    gst_amount        DECIMAL(18, 2) NOT NULL,
    total_amount      DECIMAL(18, 2) NOT NULL,
    paid_amount       DECIMAL(18, 2) NOT NULL,
    open_amount       DECIMAL(18, 2) NOT NULL,
    days_past_due     INT            NOT NULL,
    aging_bucket      VARCHAR(10)    NOT NULL,
    aging_bucket_sort INT            NOT NULL,
    status            VARCHAR(15)    NOT NULL
);

/* ---------- ETL audit ---------- */

CREATE TABLE etl_run (
    run_id            INT          NOT NULL PRIMARY KEY,
    started_at        VARCHAR(19)  NOT NULL,
    finished_at       VARCHAR(19)  NULL,
    snapshot_date     DATE         NOT NULL,
    status            VARCHAR(10)  NOT NULL,               -- SUCCESS or FAILED
    gl_rows_loaded    INT          NULL
);

CREATE TABLE dq_result (
    run_id            INT           NOT NULL,
    check_name        VARCHAR(60)   NOT NULL,
    stage             VARCHAR(10)   NOT NULL,              -- staging or warehouse
    severity          VARCHAR(7)    NOT NULL,              -- ERROR or WARNING
    passed            INT           NOT NULL,
    failing_rows      INT           NOT NULL,
    detail            VARCHAR(400)  NULL
);
