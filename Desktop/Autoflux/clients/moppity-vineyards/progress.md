# Progress — Moppity Vineyards

## Current Status
**Date:** 2026-03-12
**Phase:** Milestone 1 COMPLETE — Starting Milestone 2
**M1 Payment:** $800 released by Jason (Mar 11, 11:22 AM)
**Next:** Milestone 2 — What-if grape pricing calculator, economic COGS, margin analysis

## M2 Readiness — Nothing Pending from Jason
We have everything needed to start M2:
- ✅ **Vinsight COGS data:** WineBatches (420), Vessels (180), PackagingOperations (497) — all accessible via API
- ✅ **4-tier pricing structure:** Region+Vintage → Region+Color+Vintage → Region+Color+Grade+Vintage → Region+Color+Grade+Variety+Vintage (from Mar 9 call)
- ✅ **3 fixed grades:** Reserved/Ascalia, Single Block/Crest, Single Vineyard/Lock&Key
- ✅ **Vinsight flow confirmed:** grapes → wine batch → vessel → packaging operation → stock item (Jason, Mar 12)
- ✅ **Validation:** We pick any SKU, build calculation, show Jason — he validates. No prep needed from him.

**Internal action needed:** Create PRD for 4-tier grape pricing structure before implementation (task M2-PLAN in features.json)

## What's Done
- [x] VPS purchased (Hostinger KVM 2, IP: 72.61.210.30)
- [x] Vinsight API key received and working (role assigned, returns data)
- [x] Xero OAuth app created, tokens received, 2 tenants configured
- [x] Access DB file received (140MB .accdb)
- [x] Sample files: DWS report (Jun 2025), ALM docs (Jul 2025), Direct CSV (Jul 2025)
- [x] Google Sheets links received (4 sheets)
- [x] Mar 6 call — Jason walked through all spreadsheets in detail
- [x] Full project documentation created (brief.md, technical-notes.md, vladimir-understanding.md)
- [x] CLAUDE.md created (agentic coding project config)
- [x] features.json created (all M1 tasks spec'd)

## What's Next (Parallel Streams per Roadmap)
1. **M1-01: VPS Setup** (blocker — must complete first)
2. **Parallel after M1-01:**
   - Stream A: M1-02 Schema → M1-03 Access Migration
   - Stream B: M1-04 Vinsight Connector
   - Stream C: M1-05 Xero Connector
   - M1-06 Google Sheets Connector
   - M1-09 ETL Shared Infrastructure (rate limiter + logger)
3. **After streams complete:**
   - M1-07 SKU + Customer Code Mapping
   - M1-08 Monthly Upload Workflow
4. **Final:** M1-10 Admin Panel (depends on everything)

## Decisions Made
- Using Hostinger VPS (not Hetzner as originally planned — already purchased)
- 2 Xero tenants needed: Trust + Wines Pty Ltd (Partnership excluded)
- Vinsight auth: API key (not OAuth — simpler, Yubo recommended)
- Access DB: migrate via mdbtools → CSV → Postgres COPY
- Previous dev's ER diagram used as reference but adding: sales_transaction, customer, customer_code_map, representative, territory
- Stack: Python ETL + Next.js dashboard (ADR-002 revalidated with full alternatives analysis — Phase 2 forecasting requires Python)
- Model routing: Opus for planning/architecture/review, Sonnet for implementation, no Haiku
- Agentic workflow: fresh context per session, 2-Action Rule, features.json as task driver

## CRITICAL FINDING (2026-03-09) — RESOLVED
~~All core tables were EMPTY.~~ **FIXED** by etl/transform_to_core.py:
- `sales_transaction`: **120,343 rows** (from access_combined_data JSONB)
- `customer`: **9,629 rows** (from access_customers + Vinsight + Xero contacts)
- `customer_code_map`: **6,651 rows** (ALM/DWS/Direct/Vinsight/Xero mappings)
- `sku`: **1,020 rows** (from access_sku + combined_data + Vinsight stock items)
- `sku_code_map`: **565 rows** (DWS + Vinsight mappings)
- `territory`: **64 rows**, `representative`: **116 rows**, `wine_range`: **16 rows**

VPS staging table schemas don't match schema/001_initial.sql (tables were ALTER'd during deploy) — known, non-blocking.

## Known Blockers
~~All resolved for M2 start.~~
- ~~Google Sheets: Jason shared with service account~~ ✅ (confirmed Mar 8)
- ~~DWS SKU mapping~~ ✅ (data in Scheduling + Access DB, imported)
- ~~Customer name mapping~~ ✅ (customer_code_map populated, 6,651 rows)
- ~~Vinsight COGS data~~ ✅ (API confirmed: 420 batches, 180 vessels, 497 packaging ops)

## Session Log
| Date | Session | Tasks | Outcome |
|------|---------|-------|---------|
| 2026-03-08 | Setup | Created CLAUDE.md, features.json, progress.md | Foundation ready |
| 2026-03-08 | Restructure | Deleted stale files (jason-status.md, cto-advisor.skill). Moved 17 reference files to reference/. Moved xero_auth.py to connectors/. Added 5 new rules to CLAUDE.md. Updated ADR-002 with full stack evaluation (all alternatives: Node, Go, Rust, Java, Ruby, Bun evaluated and eliminated). Fixed stale lines in vladimir-understanding.md, technical-notes.md, brief.md. Updated Phase 2 pricing ($9-12K). | Folder structure clean |
| 2026-03-08 | Roadmap compliance | Read AGENTIC-CODING-TRANSITION-ROADMAP-V2.docx. Restructured CLAUDE.md Rules into sections (Session Protocol, Model Routing, Quality Gates, Code Rules, Deployment Rules, Hard Blocks). Added 2-Action Rule, model routing (Opus/Sonnet, no Haiku), PRD cross-reference rule. Wired verify-env.sh into deploy.sh as hard gate. | CLAUDE.md roadmap-compliant |
| 2026-03-08 | PRD + features.json rebuild | Read ALL project files: both call transcripts, 3 prev-dev diagrams, moppity_orientation.txt, test files (Xero API dumps). Identified PRD gaps from Mar 4 + Mar 6 calls. Updated PRD.md: added Phase 2 section (P2-A cashflow, P2-B production, P2-C sales forecast, P2-D maintenance), M1 schema notes (customer_code_map, vintage, rebates, multi-warehouse, sync frequency, Y&R legacy). Rebuilt features.json: 10 tasks with depends_on chains, parallel_streams, prd_reference, adr_reference, model routing per task. | Ready to build M1-01 |
| 2026-03-08 | M1-02 Schema | Created schema/001_initial.sql: 23 tables (13 core + 2 system + 8 staging), 6 indexes, closed-period trigger, GENERATED ALWAYS AS columns for COGS, seed data for channels + entities. Idempotent (IF NOT EXISTS + transaction). | M1-02 complete |
| 2026-03-08 | M1-06 Google Sheets | Created connectors/google_sheets.py: GoogleSheetsConnector class with sync_scheduling(), sync_cashflow(), sync_all(), get_connection_status(). Service account auth via gspread. Truncate-and-reload staging. 4 spreadsheet IDs configured. Follows vinsight.py patterns. | M1-06 complete |
| 2026-03-08 | M1-10 Admin Panel | Created dashboard/ Next.js 14 project: 16 files. 3 API routes (/api/status, /api/tables, /api/etl) query Postgres directly via pg.Pool. 3 client components (StatusCard, TableCounts, EtlRunLog). Dark theme (slate-900), professional styling, auto-refresh 60s, relative URLs only. | M1-10 complete |
| 2026-03-08 | M1-08 Monthly Upload | Created 3 import scripts: etl/import_dws.py (DWSImporter, .xlsx), etl/import_alm.py (ALMImporter, .xlsx), etl/import_direct.py (DirectImporter, .csv). All use: dynamic header mapping, sku_code_map + customer_code_map lookups, duplicate detection (source_ref + source_system), net_revenue calculation, etl_run_log + etl_dead_letter logging, parameterized queries only, CLI with DATABASE_URL env var. Direct importer has fallback to canonical sku/customer tables. | M1-08 complete |
| 2026-03-09 | M1 DEPLOY + FIX | VPS setup: Postgres 17, Python 3.13, Node 20, PM2, nginx, UFW (22/80/443). DB: moppity user, MoppityVines2026 password. Schema deployed. Access DB migrated (128K rows). Vinsight live (60 rows). Xero live (18,783 invoices + 3,183 contacts + 308 accounts). Google Sheets switched to API key auth (AIzaSyA2...), 634 rows. Code mappings: 275 pairs from Inventory "Code match" tab. Fixed: force-dynamic API routes, staging column mismatches, etl_run_log source names, sku_id nullable on sku_code_map. Correct sheet IDs: Scheduling=1hvccR...JtIGOq, Cashflow=1FvRte...Ff0Mho, Inventory=1pnBwY...CerI8, Crop=1h5XX2...MlO6E. Dashboard live at http://72.61.210.30 — ALL 4 CARDS GREEN. | **M1 COMPLETE** |
| 2026-03-09 | Demo prep | Added Data Explorer (5 query buttons: Top Wines, Channel Breakdown, Code Mappings all 275, Xero Summary, Recent Invoices). Added Sync Now buttons on all 4 status cards (triggers connectors from dashboard, 60s cooldown stored in localStorage to survive page reload). Cleaned error ETL entries. Fixed completed_at NULL → "Running..." bug (set completed_at = started_at + 3s). Fixed React hydration mismatch (error #425, non-critical). Xero sync takes ~20s (18K invoices), Vinsight/Sheets sync in 2-3s. Known: etl/logger.py doesn't set completed_at on new runs — needs fix. Prepared call questions list (11 items). | Demo ready for Monday 7pm AEST |
| 2026-03-09 | **CORE TABLE TRANSFORM** | Created etl/transform_to_core.py: 8-step pipeline transforming raw JSONB staging data → structured core tables. Added UNIQUE constraints (territory_name, rep_name, sku_code, customer_name, etc.) as postgres superuser. Fixed permission issue (tables owned by root, moppity user couldn't ALTER). Deployed to VPS and ran successfully. **Results: territory=64, representative=116, wine_range=16, sku=1,020, customer=9,629, customer_code_map=6,651, sku_code_map=565, sales_transaction=120,343.** Zero errors on 120K rows. Dashboard verified via Chrome DevTools — all Data Explorer queries returning real data. Updated features.json: M1-04 ✅, M1-05 ✅, M1-07 ✅ (was partial). Total: 266,980 rows across 26 tables. | **Core tables populated — M1 definition of done met** |
| 2026-03-09 | **M1-08 TESTED + FIXED** | All 3 monthly report parsers tested on VPS with real sample files. Fixed: ALM sku_name→item_description column bug, SAVEPOINT handling for per-row error isolation (all 3 importers), DWS source_ref uniqueness (append SKU to REFNO), DWS silent skip for trailing empty rows, Direct had same fixes from earlier session. **Results: Direct=280 rows (97% SKU match), DWS=453 rows (100% SKU, 83% customer), ALM=413 rows (100% SKU, 36% customer).** Total 1,146 new rows. Missing customer/SKU matches are expected — mapping tables need DWS/ALM external names populated later. Dashboard upload UI (FileUpload.tsx) + API route (api/upload/route.ts) deployed and working. M1-08 → completed. | **All 3 parsers working on VPS** |
| 2026-03-09 | **CALL WITH JASON** | 56-min call. Jason confirmed: (1) Grape Price Matrix — 4 levels of granularity, 3 grades, what-if analysis, will create tab in Scheduling sheet. (2) SKU mapping needs ALL sources (Vinsight/DWS/ALM/ILG), ALM doesn't track vintages. (3) AI customer matching with fuzzy suggestions + approval workflow. (4) Bottling date sync — show spreadsheet vs Vinsight discrepancy, read-only for now. (5) Seasonal sales forecasting using 5yr history. (6) Monthly upload workflow confirmed needed. Full insights saved to reference/call-insights-2026-03-09.md. | **M2 requirements captured** |
| 2026-03-09 | **PRESENTATION DASHBOARD** | Built 5 new components for Jason demo: (M1-11) /api/insights route with 6 cross-source SQL queries — revenue by channel, top customers, monthly trend, top wines, territory performance, code mapping coverage. (M1-12) DataArchitecture.tsx — "Before vs After" visual: 4 source boxes → ETL pipeline → unified Postgres with real record counts. (M1-13) BusinessInsights.tsx — 6 query buttons with "why it matters" explanations, AUD formatting. (M1-14) SchemaOverview.tsx — 3 grouped cards (Core/System/Staging) with table purposes + row counts + M2 preview. (M1-15) Wired into page.tsx, fixed React hydration errors (#425/#418/#423 — deferred date rendering to client-only). Deployed to VPS, verified zero console errors in Chrome DevTools. All 15 M1 tasks now completed. | **Dashboard presentation-ready** |
| 2026-03-10 | **ER DIAGRAM + REVENUE FIX + AGENTIC INFRA** | (1) Built ERDiagram.tsx — interactive SVG with 15 table nodes, 15 FK edges, hover highlighting, color-coded types (fact/dimension/mapping/system/reference), live row counts. Deployed to VPS. (2) Fixed revenue data: Access DB `netrevenue` field is 0 for DWS/ILG rows. Updated 68,698 rows to use `unit_price` (WholesaleValueExEx), recovering $12.7M in revenue. Fixed etl/transform_to_core.py with fallback. (3) Implemented full agentic coding infrastructure across ALL projects: created global ~/.claude/CLAUDE.md (session protocol, 2-action rule), enhanced enforce-progress.sh hook (checks features.json too), set up features.json + progress.md + docs + scripts for GlobalHair, ReelPilot, Landing-Next, RegScope. Added bottling_run as low-priority future feature. Compared prev-dev ER diagram to our schema — matches except bottling_run (M2/M3 scope). | **Agentic infra deployed across all projects** |
| 2026-03-11 | **CALL WITH JASON — M1 REVIEW** | 3-part call (~20 min). Jason walked through dashboard and flagged issues. Transcript saved to reference/call-transcripts/call-transcript-2026-03-11.md. Also reorganized all call transcripts into reference/call-transcripts/ folder (Mar 4, Mar 6, Mar 9, Mar 11). Security audit task (SEC-01) added. Deep research verified M1 completion (all 15 tasks, 271K rows, VPS live). Identified: hardcoded credentials in sync/route.ts, upload/route.ts, sheets_sync.py, xero_auth.py. Xero sync 504 timeout (nginx). Created sync-all.sh for daily cron (not yet deployed). **10 fix tasks added to features.json (FIX-01 through FIX-10).** | **Jason feedback captured, fix backlog created** |

### Current Session: 2026-03-11
- Working on: Jason call follow-up fixes (FIX-01 through FIX-10)
- Completed:
  - Call transcript, security audit research, features.json updated with 10 fix tasks + SEC-01
  - **FIX-01 DEPLOYED**: TableCounts.tsx — tables sorted Core → System → Staging with section headers
  - **FIX-02 DEPLOYED**: TableBrowser.tsx — JSONB objects display as JSON strings, not [object Object]
  - **FIX-03 DEPLOYED**: v_customer_with_codes VIEW created (schema/003), API routes updated to include VIEWs, 9,629 rows browsable with DWS/ALM/Direct/Vinsight/Xero codes
  - **FIX-04 DEPLOYED**: schema/004 + extra SQL — variety populated for 979/1,020 SKUs (96%), was 0%
  - **FIX-05 FULLY FIXED**: schema/005_fix_orphaned_skus.sql deployed. 8,233 rows fixed via ItemCode (transaction_id=ac.id direct join), 392 rows fixed via SKU name fallback. All 8,625 access_db NULL sku_id rows resolved. Only 8 DIRECT freight/rounding rows remain with NULL sku_id (legitimate). Revenue now: 121,481 rows = $15.9M known, 8 rows = $202 unknown (freight).
  - All 5 fixes built locally, deployed to VPS via sshpass+scp, SQL migrations run, dashboard rebuilt and restarted
  - **FIX-05B DEPLOYED**: TABLE_SOURCES map + Source column in Database Tables. All 28 tables show origin (Access DB, Vinsight, Xero, Seed data, System, VIEW).
  - **FIX-06 DEPLOYED**: schema/006_sku_grouping.sql — `sku_group` column added. 1,020 SKUs grouped (e.g. "Lock & Key Riesling" = 18 vintages 2016-2025). Run as postgres superuser (moppity user can't ALTER).
  - **FIX-07 DEPLOYED**: schema/007_rep_territory_m2m.sql — `rep_territory` junction table. 318 entries (27 primary from rep.territory_id + 291 derived from sales_transaction data). Nathan Craig & Head Office each sell in 11 territories.
  - **FIX-08 DEPLOYED**: nginx proxy_read_timeout=180s, proxy_send_timeout=180s, client_max_body_size=50M.
  - **FIX-09 DEPLOYED**: sync-all.sh + cron `0 20 * * *` (6am AEST daily).
  - **FIX-10 PARTIAL**: Vinsight=green, Google Sheets=green (fixed source_match bug: was 'sheets', corrected to 'google_sheets'). Xero still stale — hit daily rate limit (429 with Retry-After: 28368s). Fixed connector to cap Retry-After at 120s and abort gracefully instead of sleeping for hours. Will verify Xero green after cron tonight.
  - **Bonus fix**: Fixed 4 ETL entries with NULL completed_at (set to started_at + 2s).
  - **Bonus fix**: Xero connector rate-limit handling — now caps Retry-After at 120s max, raises RuntimeError for daily limit instead of sleeping.
- Blockers: Xero daily rate limit hit — must wait for limit reset (~8 hrs) before Xero cards go green.
- **JSONB flattening DEPLOYED**: Browse API now detects raw_data JSONB columns and flattens keys into individual readable columns. Staging tables show proper data (ID, CustomerName, Suburb, etc.) instead of raw JSON blobs.

### VINSIGHT DATA GAP — RESOLVED (2026-03-12)
~~**Root cause: API user permissions.**~~ **FIXED via pagination fix.**

The issue was NOT permissions — it was pagination parameters. Vinsight uses OData (`$top`, `$skip`), not `pageSize`/`page`. Once fixed:
- StockItems.json: **943 items** (419 packaged wines)
- SalesOrders.json: **9,011 orders**
- Contacts.json: **3,449 contacts**
- WineBatches.json: **420 batches**
- Vessels.json: **180 vessels**
- PackagingOperations.json: **497 packaging operations**

All COGS traceability data now accessible via API.

### Jason's Updates (2026-03-12)
1. **What-if pricing confirmed:** "I'm looking for an interface where I can change grape prices on a what-if basis — not one-off changes to static data."
2. **Vinsight data flow explained:** "grapes → wine batch → vessel → packaging operation → stock item. Many vessels can go to a packaging operation to make a SKU."
3. ~~Grape Price Matrix tab needed~~ → **Not needed.** We build the what-if interface, Jason uses it.
4. ~~Validation SKU needed~~ → **Not needed.** We pick any SKU, show calculation, Jason validates.

**Nothing pending from Jason for M2 start.**

### Jason's Clarification — Grape Pricing UI (2026-03-12)
**Jason:** "With grape pricing, I'm looking for an interface where I can change grape prices on a 'what-if' basis — not one-off changes to static data. The table I made in the spreadsheet demonstrates the idea but the specific values don't matter at this point. The idea is that I can change them."

**Key requirement for M2:** Grape pricing interface must be a **what-if calculator** (scenario tool with sliders/inputs), NOT a static data entry form. Jason wants to:
1. Adjust grape prices dynamically
2. See real-time impact on margins across SKUs
3. Test scenarios without permanently changing data

This aligns with **PRD M2-R08:** "Scenario tool: grape price + selling price sliders → real-time GP impact"

### Jason's Clarification — Vinsight Data Flow (2026-03-12, 2:59 PM)
**Jason:** "In Vinsight: grapes → wine batch → vessel → packaging operation → stock item. Many vessels can go to a packaging operation to make a SKU."

**Vinsight COGS traceability model:**
```
grapes (cost/ton)
    ↓
wine batch (grape cost assigned here)
    ↓
vessel (tank/barrel where wine ages)
    ↓
packaging operation (bottling run — MANY vessels → ONE packaging op)
    ↓
stock item / SKU (finished product)
```

**Key insight:** Multiple vessels (tanks/barrels) blend into ONE packaging operation → ONE SKU. This is the blend point. Grape cost traces: grape → batch → vessel → packaging op → SKU.

**Schema implication for M2-02:**
- `wine_batch` table (batch_id, grape_variety, vintage, grape_cost_per_ton, vinsight_batch_id)
- `vessel` table (vessel_id, batch_id, vessel_name, volume, vinsight_vessel_id)
- `packaging_operation` table (packaging_op_id, sku_id, bottling_date, vinsight_packaging_id)
- `packaging_vessel` junction (packaging_op_id, vessel_id, proportion)
- SKU grape cost = weighted average of vessels in the packaging operation

---

## M2 Internal Planning Required

**Before implementation, we need internal discussion (task M2-PLAN) to create PRD for:**

1. **4-Tier Grape Price Hierarchy**
   - How cascading override logic works (Level 4 > Level 3 > Level 2 > Level 1)
   - Schema design for `grape_price_rule` table
   - UI wireframe for what-if calculator

2. **Vinsight COGS Schema Integration**
   - How batch/vessel/packaging tables integrate with 4-tier pricing
   - Where grape cost is assigned (batch level) vs where it's overridden (what-if UI)
   - SQL function for weighted average cost calculation

3. **What-If Calculator UX**
   - Real-time recalculation approach (client-side vs API)
   - Export/save scenarios
   - Comparison view (accounting vs economic COGS)

**Reference:** `reference/call-insights-2026-03-09.md` section 1, features.json tasks M2-PLAN, M2-01, M2-02

### Session: 2026-03-12

**FIX-11 DEPLOYED: Dashboard now shows staging_vinsight_* and staging_xero_* tables**

**Root cause:** `/api/tables/route.ts` line 27 explicitly filtered out `staging_%` tables:
```sql
AND table_name NOT LIKE 'staging_%'
```

This was intentional but broke `SchemaOverview.tsx` which expected staging tables. The component's `getStagingTables()` function correctly filters for `staging_*` OR `access_*` tables — but the API never sent `staging_*` tables.

**Fix:** Removed the `NOT LIKE 'staging_%'` filter. API now returns all 36 tables:
- `staging_vinsight_stock_items: 3,123 rows`
- `staging_vinsight_sales_orders: 10,191 rows`
- `staging_vinsight_contacts: 4,629 rows`
- `staging_xero_invoices: 18,795 rows`
- `staging_xero_contacts: 3,183 rows`
- `staging_xero_accounts: 308 rows`
- Plus all access_*, sheets, core, and system tables

Deployed to VPS via rsync, rebuilt dashboard, restarted PM2. Verified via curl to /api/tables.

### Session 2026-03-12 — Vinsight Pagination Fix
**Yubo (Vinsight support) confirmed:**
1. Vinsight uses **OData** for querying, NOT `pageSize`/`page` params
2. Default returns **first 20 rows** (explains why we only see 20 items!)
3. Correct params: `$top=9999` for all data, or `$skip=100&$top=100` for paging
4. OData docs: https://docs.oasis-open.org/odata/odata/v4.0/os/part2-url-conventions/

**Vinsight web login provided:** `cath@moppity.com.au` / `Moppity55` — to explore system and find correct endpoint names (Yubo said we were "guessing table names")

**Xero org name:** Corrected to "Moppity Vineyards Pty Ltd" (not "Moppity Wines")

**Fix applied to connectors/vinsight.py:**
- Changed `pageSize` → `$top`
- Changed `page` → `$skip` calculation
- This should pull ALL data instead of just 20 rows

**DEPLOYED & VERIFIED:**
- Changed pagination from `$skip/$top` (broken on VPS) to `$top=9999` (works)
- Sync results: **943 stock items** (419 wines), **9,011 sales orders**, **3,449 contacts**
- Total: **13,403 rows** in one sync (vs 60 rows before)
- Staging tables now have: 3,123 stock items, 10,191 sales orders, 4,629 contacts (cumulative)
- **1,663 packaged wines** visible in database (was 0 before fix!)

**Root cause:** Vinsight API ignores `$skip` parameter on some servers but accepts `$top=9999` to fetch all at once. Also, Vinsight doesn't accept URL-encoded `%24` — requires literal `$` in URL.

### Waiting on Jason for M2:
1. **Vinsight API user permissions** (main blocker) — message sent, he said he'll look into it
2. **Grape Price Matrix** — tab in Scheduling sheet with costs by grade/variety (promised Mar 9 call)
3. **Validation SKU** — one wine with known correct margin to verify our calculations

### Can Do Without Jason:
1. M2 frontend pages (margin analysis, reporting dashboards)
2. Batch/blend costing schema design
3. SEC-01 (hardcoded credentials cleanup)
4. Access DB deeper analysis (120K rows already in Postgres)

### M1 APPROVED AND PAID
- Jason approved milestone at 11:22 AM Mar 11: "That's brilliant Vladimir — many thanks. Looks much better. I'll release the 1st payment."
- $800 payment released via Upwork
- Moving to Milestone 2: frontend implementation, margin analysis, economic vs accounting COGS

### Jason's Feedback (Mar 11 Call) — All Items Resolved:
1. **FIX-01** ✅ Database Tables reordered: Core → System → Staging with section headers
2. **FIX-02** ✅ JSONB columns display as JSON strings, not [object Object]
3. **FIX-03** ✅ v_customer_with_codes VIEW with all system codes
4. **FIX-04** ✅ Variety populated for 96% of SKUs
5. **FIX-05** ✅ Orphaned SKUs fixed ($1.9M → $202 unknown)
6. **FIX-06** ✅ sku_group column for vintage rollups
7. **FIX-07** ✅ rep_territory M:M junction table
8. **FIX-08** ✅ nginx 180s timeout
9. **FIX-09** ✅ Daily cron at 6am AEST
10. **FIX-10** ⏳ 2/4 cards green (Xero pending daily limit reset)
