# Business Requirements Document (BRD)

**Project:** NovaFresh Consumer Goods — FMCG Sales & Distributor Lakehouse
**Version:** 1.0
**Status:** Draft for sign-off
**Author:** Data Engineering (portfolio build)
**Last updated:** 2026-10-07

---

## 1. Purpose and context

NovaFresh Consumer Goods sells FMCG products through ~500 distributors to ~50,000 retail
outlets across India. Sales, distributor and product data are produced by separate
upstream systems (distributor portals, ERP, pricing service) and land in different
formats. There is no single governed store for this data.

This document defines what the lakehouse must do, how we will prove it works, and what
constraints we are building under. It is the contract between the business problem and
the code in this repository. Every requirement here maps to a build phase and an
artifact (see §17 traceability matrix).

## 2. Problem statement (current state)

| # | Pain today | Business impact |
|---|---|---|
| P1 | Data arrives in CSV, JSON and log text from multiple systems, with no schema contract | Analysts spend time figuring out formats instead of analysing |
| P2 | Analysts clean and join data manually in Excel | Errors, no repeatability, key-person dependency |
| P3 | Reports are 3–4 days late | Decisions are made on stale data |
| P4 | Sales and Finance numbers do not match | Loss of trust in reporting; reconciliation meetings |
| P5 | History is overwritten when a customer/product changes | Cannot answer "what did we believe at the time" |
| P6 | Everyone can see distributor phone/email | Privacy exposure, no least-privilege access |

## 3. Objectives

1. **Centralise**: one governed lakehouse (Unity Catalog) as the single source of truth for distributor sales.
2. **Automate**: daily ingestion to analytics-ready Gold tables with no manual steps.
3. **Trust**: reject bad records to quarantine, report rejection counts, and score data quality daily.
4. **Preserve**: full history of distributor/product changes (SCD Type 2) and correct late data without duplicates.
5. **Govern**: regional row-level access and phone/email masking enforced in the platform, not in reports.
6. **Engineer properly**: everything in Git, tested, and promoted dev → prod by CI/CD.

### Success criteria

| ID | Criterion | Target | How measured |
|----|-----------|--------|--------------|
| SC1 | Gold tables available after daily run | By 06:00 IST (run window 02:00–06:00) | Job end timestamp |
| SC2 | End-to-end daily run duration | < 30 minutes at 1x volume | Workflow run duration (record real number) |
| SC3 | Duplicate orders in Silver | 0 | `SELECT count(*) FROM dup check` |
| SC4 | Bad records quarantined, not silently dropped | 100% of rejected rows present in quarantine table | Rejection count = quarantine count |
| SC5 | Sales vs Finance reconciliation variance | < 0.5% (target, to be validated) | Gold reconciliation query |
| SC6 | SCD2: no attribute update lost | Every detected change creates a new version | SCD2 audit query |
| SC7 | Reprocessing the same day twice | Same result, no duplicate rows (idempotent) | Re-run comparison |

## 4. Scope

### In scope
- Daily CSV sales ingestion (Auto Loader, schema evolution).
- REST API JSON ingestion for product price/promo.
- Unstructured `.log` ingestion.
- Validation, quarantine, deduplication, late/corrected record handling (MERGE).
- SCD2 for distributor and product dimensions.
- Gold star schema + KPI tables.
- Unity Catalog governance: catalog/schema per layer, row filter on region, column mask on phone/email.
- Orchestration with retries and alerts, optimization + time travel.
- CI/CD: pytest, lint, GitHub Actions + Databricks Asset Bundles (dev → prod).
- Synthetic data generation with injected data problems.

### Out of scope
- Real production cutover, real vendor integrations, ML forecasting.
- Streaming (micro-batch) SLAs — we design for daily batch; Auto Loader is used in
  `availableNow` trigger mode so it can later be switched to continuous streaming.
- Power BI report authoring (only the Gold contract for it).
- Multi-cloud: Databricks only, single workspace per environment.

## 5. Personas and access matrix

| Persona | Needs | Region visibility | Contact data |
|---|---|---|---|
| Regional sales manager | Sales KPIs for their region | Own region only (row filter) | Masked |
| National sales head | All regions | All | Masked |
| Data analyst (Gold) | Ad-hoc SQL on Gold | All | Masked |
| Finance reconciler | Sales vs Finance | All | Masked |
| Data engineer / pipeline owner | Full read/write across layers | All | Unmasked (privileged) |
| Auditor | Lineage + history | All | Unmasked (privileged, read-only) |

Privileged vs non-privileged is enforced by group membership in Unity Catalog.

## 6. Functional requirements

Priority: **M** = Must, **S** = Should, **C** = Could.

### BR1 — Daily distributor sales CSV ingestion (M)
System shall automatically pick up new sales CSV files (one file per distributor per
day) without a file list being maintained, and record ingestion metadata.
**Acceptance criteria:**
- New files dropped into the landing path appear in Bronze without manual restart of code (pipeline/job picks them up on next trigger).
- Each Bronze row carries `ingest_time` and `source_file`.
- A file dropped twice is not processed twice (Auto Loader checkpoint).
- Re-running the ingestion for an existing date does not create duplicate Bronze rows (idempotent).

### BR2 — Product price/promo REST API ingestion (M)
System shall ingest product price and promotion JSON from a REST API into Bronze.
**Acceptance criteria:**
- API response is persisted raw (as-received JSON) with `ingest_time`.
- A retry after failure does not create duplicate records for the same fetch window.
- Schema drift in the API response (new field) is tolerated (`allowOverwrite`/schema evolution).

### BR3 — Application/pipeline log ingestion (S)
System shall ingest unstructured `.log` files into a Bronze table for troubleshooting.
**Acceptance criteria:**
- Raw line-level text stored with `source_file`, `ingest_time`.
- Parsed fields: timestamp, level, component, message (best-effort; unparsed lines kept raw).

### BR4 — Reject / quarantine bad records and report counts (M)
System shall route records failing validation rules to a quarantine table and publish
rejection counts per rule, per run.
**Acceptance criteria:**
- Rules DQ1–DQ7 (§10) evaluated on every Silver run.
- Failed records land in `silver.quarantine_sales` with `rule_id`, `failed_value`, `run_id`, `quarantine_time` — never silently dropped.
- A rejection summary table records `run_id`, `rule_id`, `rejected_count`, `total_count`.
- DQ score published for the day (BR7).

### BR5 — History of distributor and product changes, SCD Type 2 (M)
System shall keep every version of distributor and product attributes with
`effective_from` / `effective_to` / `is_current`.
**Acceptance criteria:**
- A change (e.g. distributor moves region mid-year) closes the current row (`effective_to` set, `is_current = false`) and opens a new row — old row is not overwritten.
- Unchanged attributes do not create a new version.
- Point-in-time query (`as of date`) returns the correct version.
- Exactly one current row per natural key.

### BR6 — Late-arriving and corrected records via MERGE (M)
System shall accept corrected/back-dated records without creating duplicates.
**Acceptance criteria:**
- Same `order_id` re-delivered with a correction updates the existing row (MERGE), not inserts.
- Late record dated 10 days ago is merged into the correct partition and reflected in Gold after re-aggregation.
- Idempotency: processing the same batch N times yields the same row count and content.
- Late-arriving dimension changes are applied to facts correctly (lookup by `order_date` to the SCD2 version valid on that date).

### BR7 — Gold analytics tables (M)
| Gold object | Grain / content |
|---|---|
| `gold.fct_daily_sales` | Order line × day, with distributor/product/region attributes joined via SCD2-as-of |
| `gold.agg_monthly_sales_region` | Month × region: units, net revenue, returns |
| `gold.agg_top10_products` | Rolling top 10 products by revenue (period parameterised) |
| `gold.agg_distributor_performance` | Distributor × period: revenue, orders, return rate, target vs actual |
| `gold.agg_return_rate` | Product/region × period return rate |
| `gold.dq_daily_score` | Per-day data quality score by rule and source |

**Acceptance criteria:** each table has a documented grain; totals reconcile to Silver
within the tolerance in SC5; refresh is part of the daily workflow.

### BR8 — Row-level and column-level access control (M)
| ID | Requirement |
|---|---|
| SEC1 | Regional managers see only rows of their assigned region. |
| SEC2 | `phone` and `email` are masked for non-privileged users. |
| SEC3 | Privileged groups (engineer, auditor) see unmasked values. |
| SEC4 | Access is granted via Unity Catalog group privileges, not in report layer. |

**Acceptance criteria:** same query, run as two different users, returns different row
sets / masked values; privilege changes take effect without redeploying code.

## 7. Non-functional requirements

| ID | Requirement | Target | Verification |
|---|---|---|---|
| NFR1 | Idempotent reprocessing | Re-run of any layer for a date = same output | Row count + checksum comparison before/after |
| NFR2 | Automatic retries and failure alerts | Job retry policy defined; failure notification raised | Force a failure, observe retry + alert |
| NFR3 | Daily run SLA | < 30 min end-to-end at 1x volume | Workflow duration (record real number) |
| NFR4 | Scalability | Design must hold at 100x row volume | Re-run generator at 100x, record runtime (record real number) |
| NFR5 | All changes via Git | No workspace-only edits for pipeline code | Branch/PR history |
| NFR6 | Automated tests | `pytest` green in CI on every PR | GitHub Actions run |
| NFR7 | Automated deployment | lint → pytest → bundle deploy dev; deploy prod on merge to main | Workflow run log |
| NFR8 | Observability | Per-run log of counts, rejects, duration; DQ score visible | Tables in `silver`/`gold` |
| NFR9 | Retention / time travel | Ability to query prior state; VACUUM retention documented | Time travel demo query |
| NFR10 | Small-file control | File counts kept bounded after each ingest | File count before/after `OPTIMIZE` (record real numbers) |

## 8. Data sources and datasets (synthetic)

All data is synthetic, generated by this repo. No real company, customer or employee data.

| Dataset | Format | Volume | Notes |
|---|---|---|---|
| Sales transactions | CSV, one file per distributor per day | 1–5M rows over 12–24 months | Core fact source |
| Distributor master | CSV/JSON snapshot | ~500 distributors, changes over time | SCD2 source; contains phone, email, region |
| Product master + price/promo | JSON from REST API | ~500 SKUs | SCD2 source; price changes, is_active flips |
| Pipeline logs | `.log` text | ~1000s of lines | Best-effort parsing |

**Injected problems (must all be detectable):**

| ID | Problem | Expected handling |
|---|---|---|
| PRB1 | ~2% duplicate `order_id` rows (within/across files) | Dedup in Silver; count reported |
| PRB2 | ~1% negative or null `quantity` | DQ rule → quarantine |
| PRB3 | Unknown `distributor_id` / `product_id` | DQ rule → quarantine |
| PRB4 | Future-dated `order_date` | DQ rule → quarantine |
| PRB5 | Same file delivered twice | No duplicate ingest (checkpoint + idempotency) |
| PRB6 | Late corrected record for an existing order | MERGE update, no duplicate |
| PRB7 | Distributor changes region mid-year | SCD2 new version; facts attribute by as-of date |
| PRB8 | New column appears in a later CSV file | Schema evolution in Auto Loader; no pipeline failure |

### Source schema — sales CSV (contract)

| Column | Type | Rules |
|---|---|---|
| `order_id` | string | required, unique per logical order |
| `order_date` | date | required, not in future (DQ) |
| `distributor_id` | string | required, must exist in distributor dim (DQ) |
| `outlet_id` | string | required |
| `product_id` | string | required, must exist in product dim (DQ) |
| `quantity` | int | required, > 0 (DQ) |
| `unit_price` | decimal(12,2) | required, >= 0 |
| `discount_pct` | decimal(5,2) | 0–100 |
| `order_status` | string | one of delivered / returned / cancelled |
| `updated_at` | timestamp | required; drives late/corrected detection |

## 9. Target architecture

```
                 ┌──────────────────────────────────────────────────────────┐
  Distributor    │  LANDING (UC Volume / ADLS raw zone)                     │
  CSV  ────────► │   /landing/sales/…csv   /landing/api/*.json  /landing/logs│
  REST API ─────►│                                                          │
  App logs ─────►└───────────────┬──────────────────────────────────────────┘
                                 │ Auto Loader (schema evolution, checkpoint)
                                 ▼
  BRONZE  raw + ingest_time + source_file + _ingest_file_hash        [Delta]
                                 │ typed, dedup, DQ rules
                                 ▼
  SILVER  cleansed facts, SCD2 dims, quarantine, DQ summary          [Delta]
                                 │ business aggregation, star schema
                                 ▼
  GOLD    fct_daily_sales, agg_*, dq_daily_score                     [Delta]
                                 │
                 ┌───────────────┴──────────────────┐
                 ▼                                  ▼
           SQL / Power BI                    Row filter + column mask
                 └──────── Unity Catalog (catalog.schemas, lineage, grants) ┘

  Orchestration: Databricks Workflows (retries + alerts)
  Delivery:      GitHub → pytest/lint → Databricks Asset Bundles → dev → prod
```

### Layer contracts

| Layer | Table naming | Mutability | Key columns added by platform |
|---|---|---|---|
| Bronze | `{source}.{table}` e.g. `bronze.sales_raw` | Append-only | `ingest_time`, `source_file`, `run_id` |
| Silver | `silver.{entity}` | MERGE (facts), SCD2 (dims) | `dq_status`, `updated_at`, SCD2 metadata |
| Quarantine | `silver.quarantine_{entity}` | Append | `rule_id`, `failed_value`, `run_id` |
| Gold | `gold.{fct/agg/dq}_*` | Overwrite by partition / MERGE | `run_id`, `refresh_ts` |

Catalog layout: `novafresh_dev` / `novafresh_prod` → schemas `bronze`, `silver`, `gold`,
`governance`, `sandbox`.

## 10. Data quality rule catalogue

Severity: **Q** = quarantine row (reject), **W** = warn but keep, **F** = fail pipeline.

| Rule | Severity | Definition | Applied to |
|---|---|---|---|
| DQ1 | Q | `order_id` is null or blank | sales |
| DQ2 | Q | `quantity` is null or <= 0 | sales |
| DQ3 | Q | `distributor_id` not in distributor dimension | sales |
| DQ4 | Q | `product_id` not in product dimension | sales |
| DQ5 | Q | `order_date` is null or > current date | sales |
| DQ6 | Q | `order_status` not in allowed set | sales |
| DQ7 | W | `unit_price` <= 0 or `discount_pct` out of 0–100 | sales |
| DQ8 | Q | duplicate `order_id` (keep latest by `updated_at`, quarantine the rest) | sales |
| DQ9 | F | source file unreadable / missing mandatory column after schema evolution | ingest |
| DQ10 | W | API payload missing optional price/promo fields | product API |

**DQ score** = `(total_rows - quarantined_rows) / total_rows × 100` per source per day,
published to `gold.dq_daily_score`.

## 11. Change data capture and late data policy

| Scenario | Handling |
|---|---|
| Distributor/product attribute change | SCD2: close current row, open new row with `effective_from` = change date |
| Corrected record for existing order (`updated_at` newer) | Silver MERGE on `order_id`, `WHEN MATCHED AND source.updated_at > target.updated_at` |
| Late-arriving order (old `order_date`, new file) | MERGE insert; Gold aggregates re-run for affected partitions |
| Late-arriving dimension change | Facts joined to SCD2 using `order_date` within `[effective_from, effective_to)` (as-of join) |
| Idempotency key | Facts: `order_id`; Dims: natural key + `effective_from`; runs: `run_id` |

## 12. Security and governance requirements

- Unity Catalog as the single metastore; three-tier namespace per environment.
- Landing data stored in UC Volumes (Free Edition serverless has no DBFS access).
- Group-based grants: `regional_managers`, `analysts`, `engineers`, `auditors`.
- Row filter function on region for `regional_managers` (SEC1).
- Column mask functions on `phone`, `email` for non-privileged groups (SEC2).
- Lineage captured automatically for all tables/views (auditable).
- Secrets (API tokens, PAT) never in code — GitHub Actions secrets / Databricks secret scope.

## 13. Orchestration, SLA and alerting

| Aspect | Definition |
|---|---|
| Schedule | Daily, 02:00 IST |
| Order | bronze → silver → dims → gold → DQ score → optimize |
| Trigger | `availableNow` streaming trigger for Auto Loader; batch tasks for the rest |
| SLA | Green by 06:00 IST; target duration < 30 min (NFR3) |
| Retries | 2 retries with exponential backoff per task |
| Timeout | 60 min per task, 120 min per workflow |
| Alert | Email/Webhook on final failure (after retries) |
| Idempotent replay | Workflow parameter `run_date` allows re-running a single day |

## 14. Platform constraints — Databricks Free Edition (honest assessment)

I verified these against the current Databricks Free Edition documentation. This is the
part where being honest matters more than sounding impressive.

| Capability we want | Free Edition reality | Our approach / fallback |
|---|---|---|
| Unity Catalog (3-tier, grants, lineage) | **Available** (serverless requires UC) | Use it — this is the backbone of Phase 7 |
| Lakeflow Declarative Pipelines (DLT) with `@expect` / `apply_changes` | **Available but limited: only 1 active pipeline per pipeline type** | Build DLT pipelines; run them sequentially (stop/reuse the single slot). Fallback if it blocks you: plain PySpark notebook jobs with identical logic — I'll give both |
| Databricks Asset Bundles + GitHub Actions | **Available** (CLI/OAuth/PAT deploy; bundles also deployable from the workspace) | Standard path; auth via user PAT (see next row) |
| Service principals / federation for CI/CD | **Restricted or unavailable for non-commercial accounts** — must verify in your workspace | Fallback: GitHub Actions authenticates with a user Personal Access Token stored as a secret. Document this trade-off in README |
| Custom clusters / classic compute | **Not available — serverless only**, quota-limited fair usage (exceeding quota shuts compute down for the day) | All benchmarks measured on serverless; keep runs short; note this when quoting numbers |
| `Trigger.ProcessingTime` / continuous streaming | **Not supported on serverless** | Auto Loader runs with `Trigger.AvailableNow()` — exactly what a daily batch job wants anyway |
| `df.cache()` / `persist()` / `CACHE TABLE` | **Not supported on serverless** | Do not use for benchmarking; measure cold |
| DBFS paths | **Limited** | Landing zone = Unity Catalog Volume |
| Relative Python imports / relative file paths in jobs | **Not guaranteed on serverless** | Use workspace-file modules with absolute imports; the bundle handles this |
| Maven coordinates (`%maven`) | **Not supported** | Use `%pip` for libraries |
| SQL warehouse | **One warehouse, 2X-Small max** | Enough for Gold/Power BI reads at this scale |
| Concurrent job tasks | **Max 5 per account** | Keep workflow task parallelism ≤ 4 |
| R / Scala | Not supported | Python only |
| Support, SLA, commercial use | **Not provided; non-commercial, learning use only** | Fine for a portfolio project — say so in the README |

**Consequences for our numbers:** everything we quote will be measured on a
quota-limited serverless environment with no caching APIs. Treat them as *indicative for
this dataset*, not as vendor benchmarks. All measurement methodology is in Phase 8;
blank cells stay blank until you run them.

## 15. Assumptions, dependencies, risks

| ID | Type | Item |
|---|---|---|
| A1 | Assumption | One Databricks Free Edition workspace; separate dev/prod *targets* via bundle config (not separate workspaces) |
| A2 | Assumption | Landing zone is a Unity Catalog Volume (no ADLS account available for free) |
| A3 | Assumption | Synthetic data only; generator runs locally in Python |
| D1 | Dependency | GitHub repo with Actions enabled |
| D2 | Dependency | Databricks CLI + PAT configured locally |
| R1 | Risk | Free Edition daily quota exhausted mid-run → mitigate: keep runs small, schedule off-peak |
| R2 | Risk | Single active DLT pipeline slot blocks parallel dev/prod pipelines → mitigate: sequential runs or PySpark fallback |
| R3 | Risk | Row filter / column mask edition gating unclear → mitigate: test in Phase 7; dynamic-view fallback works regardless |
| R4 | Risk | Synthetic data too clean to impress in review → mitigate: injected problems PRB1–PRB8 are all verifiable |

## 16. Definition of Done (per phase)

1. Code exists in the correct layer folder and follows the naming convention.
2. It runs end-to-end on the Free Edition workspace (or locally for generator/tests).
3. Verification queries/commands in the phase doc were executed and their output recorded.
4. Known errors encountered are documented in the phase notes.
5. `pytest` (from Phase 9 onward) passes; lint passes.

## 17. Traceability matrix

| Requirement | Phase | Artifact |
|---|---|---|
| BR1 Auto Loader ingest | 3 | `src/bronze/ingest_sales_autoloader.py` |
| BR2 REST API ingest | 3 | `src/bronze/ingest_product_api.py` |
| BR3 Log ingest | 3 | `src/bronze/ingest_logs.py` |
| BR4 quarantine + counts | 4 | `src/silver/validate_sales.py`, `silver.quarantine_sales`, `silver.dq_rejection_summary` |
| BR5 SCD2 | 5 | `src/silver/scd2.py` (`apply_changes` + MERGE) |
| BR6 MERGE late data | 5 | `src/silver/merge_sales.py` |
| BR7 Gold tables/KPIs | 6 | `src/gold/*` |
| SEC1–4 access control | 7 | `src/common/security_sql.sql` |
| NFR3/NFR4/NFR10 optimization | 8 | `docs/optimization_results.md` |
| NFR5–7 CI/CD | 9 | `databricks.yml`, `resources/`, `.github/workflows/` |
| P1–P6 narrative, results | 10 | `README.md` |

## 18. Glossary

| Term | Meaning |
|---|---|
| Lakehouse | Architecture combining lake storage + warehouse semantics on Delta Lake |
| Medallion | Bronze (raw) / Silver (cleansed) / Gold (business) layering |
| Auto Loader | Structured Streaming source that incrementally discovers new files |
| SCD Type 2 | Slowly Changing Dimension: every change becomes a new row with validity dates |
| MERGE | Delta upsert: insert/update depending on key match |
| Quarantine | Table holding rejected rows so nothing is silently lost |
| DQ score | % of rows passing all quarantine rules for a day |
| Unity Catalog | Databricks governance layer: 3-level namespace, grants, masks, lineage |
| Asset Bundle (DAB) | YAML definition of jobs/pipelines for CI/CD promotion |
| `availableNow` | Streaming trigger that processes all available data then stops |
