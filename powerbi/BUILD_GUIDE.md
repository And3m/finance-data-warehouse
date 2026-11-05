# Building the Power BI report

The warehouse publishes its star schema and reporting views as CSV files in `exports/`
(and as live tables if you load into SQL Server). This guide builds the report in Power BI Desktop.

## 1. Get the data

Option A, SQL Server (recommended):
1. Run the ETL with `DW_URL` pointing at your SQL Server database (see the main README).
2. In Power BI Desktop choose **Get data > SQL Server**, enter the server and `FinanceDW`, and select Import.
3. Select the five `dim_*` tables, the three `fact_*` tables, and the views you want as separate visuals.

Option B, CSV files:
1. Run the ETL with no `DW_URL`. It writes every table and view to `exports/`.
2. Choose **Get data > Folder** or **Text/CSV** and load the same tables.
3. In Power Query set `full_date` to Date and every amount column to Fixed Decimal Number.

## 2. Model

Create these relationships (many to one, single direction):

| From | To |
|---|---|
| fact_gl[date_key] | dim_date[date_key] |
| fact_gl[account_key] | dim_account[account_key] |
| fact_gl[cost_center_key] | dim_cost_center[cost_center_key] |
| fact_ar_invoice[customer_key] | dim_customer[customer_key] |
| fact_ar_invoice[due_date_key] | dim_date[date_key] (active) |
| fact_ap_invoice[vendor_key] | dim_vendor[vendor_key] |
| fact_ap_invoice[due_date_key] | dim_date[date_key] (active) |
| fact_ap_invoice[invoice_date_key] | dim_date[date_key] (inactive, used by DPO) |

Then mark `dim_date` as the date table on `full_date`, and sort `month_label` by `fiscal_month_no`.

## 3. Measures

Create a table named `_Measures` and add every measure from `measures.dax`.

## 4. Report pages

1. **P&L overview**: cards for Revenue, Gross Margin %, Net Profit, and Net Margin %; a clustered column chart of Revenue and Net Profit by month_label; a matrix of pnl_group and account_name by cost_center_name.
2. **Cash and working capital**: a line chart of Cash Balance by month; cards for DSO and DPO; Receivables, Payables, and Inventory balances.
3. **Receivables aging**: a stacked bar of AR Open Amount by aging_bucket (sorted by aging_bucket_sort); a customer table with open amount, overdue amount, and days past due; a city slicer.
4. **Payables aging**: the same layout for vendors.
5. **Controls**: cards for Trial Balance Check and AR Reconciliation Difference, plus a table of `dq_result` from the last ETL run.

Save the file as `powerbi/Finance_DW.pbix` and add screenshots to `docs/`.
