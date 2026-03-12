# Moppity Vineyards — Product Requirements Document

## Last Updated: 2026-03-08
## Owner: Vladimir (Autoflux) | Client: Jason Brown

---

## 1. Product Vision

Give Jason Brown a single URL where he can see all his business data in one place — inventory, sales, margins, cash position — instead of opening 4 different systems and spending hours in Excel.

**One sentence:** Replace 4 disconnected systems with one dashboard that answers "what's my margin on Lock & Key Shiraz?" in one click.

---

## 2. Users

| User | Role | Needs | Frequency |
|------|------|-------|-----------|
| Jason Brown | Managing Director | View dashboards, run scenarios, answer business questions | Daily |
| Jason's wife/partner | Co-owner | Same as Jason, read-only | Weekly |
| Vladimir (Autoflux) | Developer/Maintainer | Debugging, ETL monitoring, schema updates | As needed |

---

## 3. Problem Statement

Jason operates a vertically integrated wine business across 3 legal entities (Vineyard Partnership → Moppity Trust → Moppity Wines Pty Ltd). His data lives in 4 systems that don't talk to each other:

1. **Vinsight** — wine production, inventory, stock on hand
2. **Xero** — accounting across 3 entities (2 needed: Trust + Wines Pty Ltd)
3. **Access Database** — 5 years of sales analytics (140MB, hitting performance limits)
4. **Google Sheets** — manual planning (scheduling, cashflow, crop planning, inventory)

To answer basic business questions, Jason manually exports data from multiple systems, matches it in spreadsheets (fighting different SKU codes across systems), and calculates margins by hand. This takes hours and is error-prone.

**The critical pain:** Grape costs in Vinsight are recorded at accounting rates ($300/ton), but real market rates are ~$2,000/ton. Without the ability to override with economic prices, all margin calculations are misleading.

---

## 4. Requirements by Milestone

### Milestone 1 — Data Foundation ($800, 7 days)

**Goal:** All 4 sources connected. Data flowing into Postgres. Admin panel shows everything green.

| ID | Requirement | Priority | Acceptance Criteria |
|----|------------|----------|-------------------|
| M1-R01 | VPS running with Postgres, Python, Node, nginx | Critical | All services responding, firewall configured |
| M1-R02 | Postgres schema supports all business entities | Critical | Entity, SKU, sales, customer, rep, territory, channel, COGS tables created |
| M1-R03 | Access DB migrated to Postgres | Critical | All core tables imported, row counts match source |
| M1-R04 | Vinsight API pulling inventory + sales data | Critical | StockItems and SalesOrders syncing, rate limits respected |
| M1-R05 | Xero API pulling invoices from 2 tenants | Critical | Trust and Wines Pty Ltd data flowing, token auto-refresh |
| M1-R06 | Google Sheets connected (read-only) | Medium | Scheduling tab data in Postgres (or "pending share" in panel) |
| M1-R07 | DWS SKU code mapping table populated | Medium | MV↔DWS code lookups working, unknown codes flagged |
| M1-R08 | Monthly upload workflow (ALM/DWS/Direct) | Medium | Sample files parse successfully, duplicates detected |
| M1-R09 | Admin panel live on VPS URL | Critical | All sources show status, last sync, row counts, errors |
| M1-R10 | ETL error handling + logging | Medium | All runs logged, failed records preserved, visible in panel |
| M1-R11 | Economic COGS schema with lock mechanism | Critical | sku_cogs table with nominated_price and lock_date |
| M1-R12 | Month-end snapshot capability | Critical | Periods can be closed, closed periods reject new data |

**Definition of Done:** Jason opens the admin panel URL → sees all sources green → can verify row counts match his source systems.

### Milestone 2 — Business Intelligence ($1,400, 10-14 days)

**Goal:** Jason clicks a wine and sees everything — margins, inventory, velocity, scenarios.

| ID | Requirement | Priority |
|----|------------|----------|
| M2-R01 | SKU → Blend → Batch → Grape traceability model | Critical |
| M2-R02 | Dual cost basis: Accounting COGS vs Economic COGS | Critical |
| M2-R03 | Contribution margin by SKU, channel, customer, rep, territory | Critical |
| M2-R04 | Inventory cover: months of stock + projected run-out date | High |
| M2-R05 | Sales velocity: rolling 3/6/12 months by SKU | High |
| M2-R06 | Bottling recommendations: cover targets vs bulk SOH | Medium |
| M2-R07 | DWS vs Vinsight reconciliation: nightly diff + alerts | High |
| M2-R08 | Scenario tool: grape price + selling price sliders → real-time GP impact. **Jason clarified (Mar 12):** Must be a what-if calculator, NOT static data entry. Changes are temporary for scenario testing, not persisted. | Critical |
| M2-R09 | Web dashboard with all views, live on VPS | Critical |

**Definition of Done:** Click Lock & Key Shiraz → see accounting GP, economic GP, inventory by channel, run-out date, velocity trend. Scenario sliders update live.

### Milestone 3 — AI Layer + Handoff ($600, 5-7 days)

**Goal:** Ask a question in English, get an accurate answer backed by real data.

| ID | Requirement | Priority |
|----|------------|----------|
| M3-R01 | AI chat embedded in dashboard (Claude Sonnet 4.6) | Critical |
| M3-R02 | Queries computed views only — no raw table access | Critical |
| M3-R03 | Every answer shows source view for transparency | Critical |
| M3-R04 | Sample prompts for common questions | Medium |
| M3-R05 | Full documentation | Medium |
| M3-R06 | 10 days free support post-handoff | Required |

**Definition of Done:** "What are my highest margin wines on an economic basis?" returns correct ranked list matching dashboard.

---

### M1 Schema Notes (from Mar 4 + Mar 6 call analysis)

The M1 schema must anticipate Phase 2 even though we don't build Phase 2 features yet:

1. **Customer name mapping** — ALM/DWS/Direct use different customer names (not just SKU codes). Add `customer_code_map` table mirroring `sku_code_map` pattern.
2. **Vintage as first-class field** — Multiple vintages of same SKU coexist in inventory. `sku.vintage` must be explicit, not buried in SKU code.
3. **Rebates + wholesale fees** — CombinedData in Access has these as explicit columns. `sales_transaction` must include `rebate_amount` and `wholesale_fee` fields.
4. **Multi-warehouse DWS inventory** — DWS holds stock in warehouses across Australia. Inventory tracking needs location dimension (defer full implementation to Phase 2, but don't block it in schema).
5. **Sync frequency** — Jason currently syncs Vinsight fortnightly, not daily. Build for daily but default cron to fortnightly.
6. **Y&R legacy naming** — Access DB references "Y&R" which is the old name for the DWS channel. ETL must handle this mapping.

---

### Phase 2 — Cashflow + Production Planning (estimated $9,000-12,000, after M1-M3)

**Context:** Jason confirmed (Mar 7, 2026) these are his top two priorities. Agreed with Vladimir to deliver as Phase 2 after M1-M3 data foundation is solid.

#### P2-A: Cashflow Forecasting ($4,000-5,000) — Jason's #1 Priority

| ID | Requirement | Priority |
|----|------------|----------|
| P2-A01 | 12-24 month forward cash projection across all 3 entities | Critical |
| P2-A02 | Revenue forecast linked to Xero actuals (currently no link) | Critical |
| P2-A03 | Expenditure timing model: cash out at purchase date, not consumption date | Critical |
| P2-A04 | Monthly cash position with forecast bank balance | Critical |
| P2-A05 | Variance tracking: forecast vs actuals | High |
| P2-A06 | Working capital requirements calculation | High |
| P2-A07 | What-if scenarios: "Can we afford this production run?" | Medium |
| P2-A08 | Consolidated view across Trust + Wines Pty Ltd + Partnership | Medium |

**Key business rule from Mar 6 call:** Dry goods cost is incurred at purchase (months before bottling), not at bottling. When labels arrive late, wine gets bottled unlabeled, then re-run through the line — doubling bottling cost. The timing model must handle this.

#### P2-B: Production & Dry Goods Management ($3,000-4,000) — Jason's #2 Priority

| ID | Requirement | Priority |
|----|------------|----------|
| P2-B01 | Bulk wine inventory table (liters by variety, vintage, source: own vs purchased) | Critical |
| P2-B02 | Bulk wine allocation to SKU bottling runs | Critical |
| P2-B03 | Dry goods inventory tracking (labels, cartons, capsules, glass, bottles) | Critical |
| P2-B04 | Dry goods depletion forecast based on bottling schedule | High |
| P2-B05 | Label print run optimization (batch multiple SKUs, bigger runs = cheaper) | Medium |
| P2-B06 | Bottling schedule with dry goods readiness check (all components in stock?) | High |
| P2-B07 | Surplus/shortage flagging per SKU (cases above/below projected need) | High |
| P2-B08 | Bulk wine shortage trigger (flag when external purchase needed) | Medium |
| P2-B09 | Vineyard block hierarchy (vineyard → block → variety → rows → vines) | Medium |
| P2-B10 | Tonnage planning with fermenter constraints (10-ton minimum) and freight (20-ton semi) | Low |

#### P2-C: Sales Forecasting ($2,000-3,000)

| ID | Requirement | Priority |
|----|------------|----------|
| P2-C01 | Automated per-SKU per-territory monthly case forecast (replacing manual process) | Critical |
| P2-C02 | Average wholesale price per territory (auto-calculated from actuals) | High |
| P2-C03 | Volume sensitivity scenarios (price up + volume down combos) | Medium |
| P2-C04 | Forecast vs actual tracking with variance alerts | High |

#### P2-D: Ongoing Maintenance ($500/month)

Monthly retainer for updates, bug fixes, data issues, new SKU onboarding.

**Specific numbers from Mar 6 call (validation data for Phase 2):**
- ACT avg wholesale: $104/case, Victoria: $79/case
- Capsule unit cost: ~$0.30
- 12-month label run: 52,000 labels; 18-month: 63,000; 6-month: 6,200
- 12-month capsule need: 80,000; 2-year: 90,000
- Fermenter minimum: 10 tons
- Semi-trailer: 20 tons
- Coppabella Crest Chardonnay: 80% in barrel, 25% new oak

---

## 5. Data Model Summary

### Source Systems → Postgres Mapping

| Source | Tables | Sync Method | Frequency |
|--------|--------|------------|-----------|
| Access DB | CombinedData, Customers, Representative, SKU, ItemDescription, tbl_Master_COGS, tbl_StocksONHand, channel tables, territory tables | One-time migration (mdbtools → CSV → COPY) | Once, then monthly upload workflow replaces it |
| Vinsight | StockItems, SalesOrders, Contacts, Products, PackagingRuns | REST API (api-key auth) | Daily sync |
| Xero | Invoices, Contacts, Accounts (Trust + Wines Pty Ltd) | OAuth 2.0 | Daily sync |
| Google Sheets | Scheduling, Cashflow, Crop 2026, Inventory | gspread (service account) | On-demand |

### Critical Business Logic in Schema

1. **3 entities** → `entity` table with `xero_org_id` per entity
2. **SKU code chaos** → `sku_code_map` table: source_system + external_code → canonical sku_id
3. **Customer name chaos** → `customer_code_map` table: source_system + external_name → canonical customer_id (ALM/DWS/Direct all use different names)
4. **Vintage as dimension** → `sku.vintage` as explicit field (multiple vintages coexist in inventory)
5. **Economic vs Accounting COGS** → `sku_cogs` table: `accounting_grape_price` vs `nominated_grape_price`, locked at `lock_date`
6. **Period stability** → `month_end_snapshot` with `period_closed` flag preventing retroactive edits
7. **Sales by channel** → `sales_transaction` linking customer, rep, territory, channel with `rebate_amount` and `wholesale_fee` fields

---

## 6. Non-Functional Requirements

| Category | Requirement |
|----------|------------|
| Performance | Admin panel loads in <3 seconds. ETL syncs complete in <5 minutes. |
| Availability | VPS uptime 99%+ (Hostinger SLA). No HA required at this scale. |
| Data freshness | Vinsight/Xero: fortnightly sync default (Jason's current cadence), build for daily. DWS/ALM: monthly (matches report cadence). |
| Security | No credentials in git. VPS firewall: 22/80/443 only. Postgres not exposed externally. |
| Cost | VPS: ~$10/mo. AI chat: ~$2-5/mo. Total ongoing: <$20/mo for Jason. |
| Maintainability | Documentation complete. Any developer can pick up the project. |

---

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Xero token expires mid-sync | Data stale until re-auth | Auto-refresh before each session. Alert in admin panel. |
| DWS/ALM report format changes | Monthly import breaks | Schema validation on upload. Error rows preserved, not dropped. |
| Jason adds new wine/SKU not in mapping table | DWS reconciliation fails for that SKU | Unknown codes flagged in admin panel for manual mapping. |
| VPS goes down | Dashboard inaccessible | Hostinger SLA. Data safe in Postgres. Can restore from backup. |
| Grape prices change retroactively | Historical margins become untrustworthy | Lock mechanism prevents this — prices frozen at bottling date. |

---

## 8. Success Metrics

| Metric | Target | How to Measure |
|--------|--------|---------------|
| Time to answer "what's my margin on X?" | <10 seconds (from hours) | Jason feedback |
| Manual Excel reconciliation work | Eliminated | Jason feedback |
| Data sources connected | 4/4 | Admin panel |
| Month-end close process | Automated snapshot | Dashboard |
| Jason satisfaction | "This is exactly what I needed" | Milestone review calls |
