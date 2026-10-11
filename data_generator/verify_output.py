"""Verify generated landing data against the manifest (PRB1-PRB8, BR1-BR3 sanity).

Usage (from repo root):
    python -m data_generator.verify_output --output data_generator/output
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

from data_generator.config import GeneratorConfig, build_config
from data_generator.generate_sales import UNKNOWN_DISTRIBUTOR, UNKNOWN_PRODUCT

PASS = "PASS"
FAIL = "FAIL"
RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append((PASS if ok else FAIL, name, detail))


def _read_all_sales(cfg: GeneratorConfig) -> pd.DataFrame:
    index = pd.read_csv(f"{cfg.manifest_dir}/sales_file_index.csv")
    frames = []
    total = len(index)
    for i, row in enumerate(index.itertuples(index=False), start=1):
        frames.append(pd.read_csv(row.path, dtype=str))
        if i % 2000 == 0:
            print(f"  ...read {i}/{total} sales files")
    return pd.concat(frames, ignore_index=True)


def verify(cfg: GeneratorConfig) -> bool:
    with open(f"{cfg.manifest_dir}/manifest.json", encoding="utf-8") as fh:
        manifest = json.load(fh)

    sales_dir = Path(cfg.sales_dir)
    sales_files = [p for p in sales_dir.rglob("sales_*.csv") if "_copy" not in p.stem]
    record("PRB5 file count", len(sales_files) == manifest["counts"]["sales_files"], f"{len(sales_files)} original sales files")

    sales = _read_all_sales(cfg)
    record("row count", len(sales) >= manifest["counts"]["sales_rows"], f"{len(sales):,} rows read")

    dup_mask = sales["order_id"].duplicated(keep=False)
    dup_ids = sales.loc[dup_mask, "order_id"].nunique()
    extra_rows = len(sales) - sales["order_id"].nunique()
    expected_min_dup = (
        manifest["sales_injections"]["duplicate_within_file"]
        + manifest["cross_file_duplicates"]["cross_file_dup_rows_modified"]
        + manifest["cross_file_duplicates"]["cross_file_dup_rows_plain"]
    )
    record("PRB1 duplicate order_ids present", dup_ids > 0, f"{dup_ids:,} order_ids appear more than once")
    record(
        "PRB1 extra rows from duplicates",
        extra_rows >= expected_min_dup,
        f"{extra_rows:,} extra rows (expected >= {expected_min_dup:,})",
    )

    qty = pd.to_numeric(sales["quantity"], errors="coerce")
    neg = int((qty < 0).sum())
    nullq = int(qty.isna().sum())
    inj = manifest["sales_injections"]
    record("PRB2 negative quantity", neg >= inj["quantity_negative"], f"{neg:,} rows (injected {inj['quantity_negative']:,})")
    record("PRB2 null quantity", nullq >= inj["quantity_null"], f"{nullq:,} rows (injected {inj['quantity_null']:,})")

    unk_d = int((sales["distributor_id"] == UNKNOWN_DISTRIBUTOR).sum())
    unk_p = int((sales["product_id"] == UNKNOWN_PRODUCT).sum())
    record("PRB3 unknown distributor_id", unk_d >= inj["unknown_distributor"], f"{unk_d:,} rows")
    record("PRB3 unknown product_id", unk_p >= inj["unknown_product"], f"{unk_p:,} rows")

    future = int((pd.to_datetime(sales["order_date"], errors="coerce") > pd.Timestamp(cfg.end_date)).sum())
    record("PRB4 future order_date", future >= inj["future_order_date"], f"{future:,} rows after {cfg.end_date}")

    disc = pd.to_numeric(sales["discount_pct"], errors="coerce")
    bad_disc = int(((disc < 0) | (disc > 100)).sum())
    record("DQ7 discount out of range", bad_disc >= inj["discount_out_of_range"], f"{bad_disc:,} rows")

    file_index = pd.read_csv(f"{cfg.manifest_dir}/sales_file_index.csv")
    pre_row = file_index[~file_index["has_extra_columns"]].iloc[0]
    post_row = file_index[file_index["has_extra_columns"]].iloc[0]
    pre_cols = set(pd.read_csv(pre_row["path"], nrows=0).columns)
    post_cols = set(pd.read_csv(post_row["path"], nrows=0).columns)
    extra = {"route_code", "salesman_code"}
    record(
        "PRB8 new columns after pivot",
        extra.issubset(post_cols) and not (extra & pre_cols),
        f"pre-pivot ({pre_row['file_date']}) cols={sorted(pre_cols)}; post-pivot ({post_row['file_date']}) added={sorted(extra & post_cols)}",
    )

    late = manifest["late_corrections"]
    late_files = [Path(p) for p in late["late_correction_files"]]
    record("PRB6 late correction files exist", all(p.exists() for p in late_files), f"{len(late_files)} files")
    late_frames = pd.concat([pd.read_csv(p, dtype=str) for p in late_files], ignore_index=True)
    original_ids = set(sales["order_id"])
    corrected_in_original = int(late_frames["order_id"].isin(original_ids).sum())
    record(
        "PRB6 corrections target existing orders",
        corrected_in_original >= late["expected_updates_of_existing_orders"] * 0.9,
        f"{corrected_in_original} of {len(late_frames)} late rows match pre-existing order_ids",
    )
    late_new = late_frames["order_id"].str.startswith("ORDLATE").sum()
    record("PRB6 brand-new backdated orders", int(late_new) == late["expected_inserts_of_new_orders"], f"{late_new} ORDLATE* rows")

    master_paths = sorted(Path(cfg.masters_dir).glob("master_*.csv"))
    master_by_date = {pd.Timestamp(p.stem.replace("master_", "")): p for p in master_paths}
    record("master snapshot files", len(master_paths) == manifest["scd2"]["snapshot_dates_count"], f"{len(master_paths)} files")

    region_events = [c for c in manifest["scd2"]["distributor_change_events"] if c["problem_id"] == "PRB7"]
    region_ok = 0
    for ev in region_events:
        change_day = pd.Timestamp(ev["change_date"])
        before_dates = [d for d in master_by_date if d < change_day]
        after_dates = [d for d in master_by_date if d >= change_day]
        if not before_dates or not after_dates:
            continue
        df_before = pd.read_csv(master_by_date[max(before_dates)])
        df_after = pd.read_csv(master_by_date[min(after_dates)])
        row_before = df_before[df_before["distributor_id"] == ev["distributor_id"]]
        row_after = df_after[df_after["distributor_id"] == ev["distributor_id"]]
        if (
            not row_before.empty
            and not row_after.empty
            and row_before.iloc[0]["region"] == ev["old"]["region"]
            and row_after.iloc[0]["region"] == ev["new"]["region"]
        ):
            region_ok += 1
    record("PRB7 region change visible in snapshots", region_ok == len(region_events), f"{region_ok}/{len(region_events)} events verified")

    feed_files = sorted(Path(cfg.api_dir).glob("product_feed_*.json"))
    record("BR2 product API feed files", len(feed_files) == manifest["counts"]["product_feed_files"], f"{len(feed_files)} JSON feeds")
    with open(feed_files[len(feed_files) // 2], encoding="utf-8") as fh:
        sample_feed = json.load(fh)
    record("BR2 feed payload shape", sample_feed["record_count"] == len(sample_feed["records"]) == cfg.product_count, f"records={sample_feed['record_count']}")

    dup_delivered = manifest["duplicate_delivered_files"]
    hash_ok = 0
    for copy_path in dup_delivered:
        original = Path(copy_path)
        stem = original.stem.split("_copy")[0]
        parent = original.parent
        candidates = list(parent.glob(f"{stem}.*"))
        if not candidates:
            continue
        h_copy = hashlib.md5(original.read_bytes()).hexdigest()
        h_orig = hashlib.md5(candidates[0].read_bytes()).hexdigest()
        if h_copy == h_orig:
            hash_ok += 1
    record("PRB5 duplicate file copies byte-identical", hash_ok == len(dup_delivered), f"{hash_ok}/{len(dup_delivered)} identical")

    log_files = sorted(Path(cfg.logs_dir).glob("*.log"))
    record("BR3 log files", len(log_files) == manifest["counts"]["log_files"], f"{len(log_files)} log files")
    sample_log = log_files[len(log_files) // 2].read_text(encoding="utf-8").splitlines()
    malformed = sum(1 for line in sample_log if line and not line[:4].isdigit())
    record("BR3 malformed log lines present", malformed > 0, f"{malformed} malformed lines in sample log")

    phones = pd.read_csv(f"{cfg.masters_dir}/{sorted(Path(cfg.masters_dir).glob('master_*.csv'))[0].name}", dtype=str)
    record("fictional contact data", phones["phone"].str.match(r"^\+91 9\d{8}$").all() and phones["email"].str.endswith("@example.com").all(), "phone format +91 9XXXXXXXX, email @example.com")

    return all(status == PASS for status, _, _ in RESULTS)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="data_generator/output")
    p.add_argument("--profile", choices=["dev", "full"], default="dev")
    args = p.parse_args()
    cfg = build_config(profile=args.profile, output_dir=args.output)

    ok = verify(cfg)
    print("")
    print("=== verification ===")
    for status, name, detail in RESULTS:
        print(f"{status:5} | {name:45} | {detail}")
    print("")
    print("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
