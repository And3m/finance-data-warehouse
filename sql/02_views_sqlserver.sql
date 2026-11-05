/* =====================================================================
   Reporting views (T-SQL). These are the tables Power BI reads.
   The ETL runs this file as-is on SQL Server and transpiles it with
   sqlglot for SQLite, so keep each view to portable ANSI constructs.
   Views are separated by a line containing only GO.
   ===================================================================== */

CREATE VIEW vw_trial_balance AS
SELECT a.account_code,
       a.account_name,
       a.account_type,
       a.statement,
       SUM(f.debit)  AS total_debit,
       SUM(f.credit) AS total_credit,
       SUM(f.amount) AS balance          -- positive = debit balance
FROM fact_gl f
JOIN dim_account a ON a.account_key = f.account_key
GROUP BY a.account_code, a.account_name, a.account_type, a.statement;
GO

CREATE VIEW vw_pnl_monthly AS
SELECT d.fiscal_year,
       d.fiscal_month_no,
       d.month_label,
       c.cost_center_name,
       a.pnl_group,
       a.account_name,
       SUM(CASE WHEN a.account_type = 'Revenue' THEN -f.amount ELSE f.amount END) AS pnl_amount
FROM fact_gl f
JOIN dim_account a     ON a.account_key = f.account_key
JOIN dim_date d        ON d.date_key = f.date_key
JOIN dim_cost_center c ON c.cost_center_key = f.cost_center_key
WHERE a.statement = 'PL'
GROUP BY d.fiscal_year, d.fiscal_month_no, d.month_label, c.cost_center_name, a.pnl_group, a.account_name;
GO

CREATE VIEW vw_pnl_summary_monthly AS
SELECT d.fiscal_year,
       d.fiscal_month_no,
       d.month_label,
       SUM(CASE WHEN a.pnl_group = 'Revenue' THEN -f.amount ELSE 0 END)            AS revenue,
       SUM(CASE WHEN a.pnl_group = 'Cost of Sales' THEN f.amount ELSE 0 END)       AS cost_of_sales,
       SUM(CASE WHEN a.pnl_group = 'Operating Expenses' THEN f.amount ELSE 0 END)  AS operating_expenses,
       SUM(CASE WHEN a.pnl_group = 'Finance Costs' THEN f.amount ELSE 0 END)       AS finance_costs,
       SUM(-f.amount)                                                              AS net_profit
FROM fact_gl f
JOIN dim_account a ON a.account_key = f.account_key
JOIN dim_date d    ON d.date_key = f.date_key
WHERE a.statement = 'PL'
GROUP BY d.fiscal_year, d.fiscal_month_no, d.month_label;
GO

CREATE VIEW vw_ar_aging AS
SELECT c.customer_id,
       c.customer_name,
       c.city,
       f.aging_bucket,
       f.aging_bucket_sort,
       COUNT(*)           AS open_invoices,
       SUM(f.open_amount) AS open_amount
FROM fact_ar_invoice f
JOIN dim_customer c ON c.customer_key = f.customer_key
WHERE f.open_amount > 0
GROUP BY c.customer_id, c.customer_name, c.city, f.aging_bucket, f.aging_bucket_sort;
GO

CREATE VIEW vw_ap_aging AS
SELECT v.vendor_id,
       v.vendor_name,
       v.category,
       f.aging_bucket,
       f.aging_bucket_sort,
       COUNT(*)           AS open_bills,
       SUM(f.open_amount) AS open_amount
FROM fact_ap_invoice f
JOIN dim_vendor v ON v.vendor_key = f.vendor_key
WHERE f.open_amount > 0
GROUP BY v.vendor_id, v.vendor_name, v.category, f.aging_bucket, f.aging_bucket_sort;
GO

CREATE VIEW vw_cash_position_monthly AS
SELECT m.fiscal_year,
       m.fiscal_month_no,
       m.month_label,
       m.cash_in,
       m.cash_out,
       m.cash_in - m.cash_out AS net_movement,
       SUM(m.cash_in - m.cash_out) OVER (ORDER BY m.fiscal_year, m.fiscal_month_no
                                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS closing_balance
FROM (
    SELECT d.fiscal_year,
           d.fiscal_month_no,
           d.month_label,
           SUM(f.debit)  AS cash_in,
           SUM(f.credit) AS cash_out
    FROM fact_gl f
    JOIN dim_account a ON a.account_key = f.account_key
    JOIN dim_date d    ON d.date_key = f.date_key
    WHERE a.account_code = '1000'
    GROUP BY d.fiscal_year, d.fiscal_month_no, d.month_label
) m;
GO
