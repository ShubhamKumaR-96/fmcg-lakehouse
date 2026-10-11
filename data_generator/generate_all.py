"""Entry point: generate the full NovaFresh synthetic landing zone.

Usage (from repo root):
    python -m data_generator.generate_all --profile dev
    python -m data_generator.generate_all --profile full --output data_generator/output_full
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

from data_generator.config import PIVOT_DATE, SALES_EXTRA_COLUMNS, GeneratorConfig, build_config
from data_generator.generate_logs import generate_logs
from data_generator.generate_late_corrections import generate_late_corrections
from data_generator.generate_masters import (
    build_distributor_history,
    build_product_history,
    write_distributor_snapshots,
    write_product_feeds,
)
from data_generator.generate_sales import build_sales_catalogue, generate_sales_files
from data_generator.inject_problems import deliver_duplicate_files, inject_cross_file_duplicates


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate NovaFresh synthetic FMCG data")
    p.add_argument("--profile", choices=["dev", "full"], default="dev")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", default="data_generator/output")
    p.add_argument("--start-date", type=lambda s: date.fromisoformat(s), default=None)
    p.add_argument("--end-date", type=lambda s: date.fromisoformat(s), default=None)
    p.add_argument("--distributors", type=int, default=None)
    p.add_argument("--avg-rows", type=float, default=None)
    return p.parse_args()


def _ensure_dirs(cfg: GeneratorConfig) -> None:
    for d in [cfg.landing_dir, cfg.sales_dir, cfg.masters_dir, cfg.api_dir, cfg.logs_dir, cfg.manifest_dir]:
        Path(d).mkdir(parents=True, exist_ok=True)


def main() -> None:
    args = parse_args()
    cfg = build_config(
        profile=args.profile,
        seed=args.seed,
        output_dir=args.output,
        start_date=args.start_date,
        end_date=args.end_date,
        distributor_count=args.distributors,
        avg_rows_per_dist_day=args.avg_rows,
    )
    _ensure_dirs(cfg)
    started = time.time()
    rng = np.random.default_rng(cfg.seed)
    fake = Faker("en_IN")
    Faker.seed(cfg.seed)

    print(f"[1/7] distributor history  profile={cfg.profile} distributors={cfg.distributor_count}")
    dist_versions, dist_changes = build_distributor_history(cfg, rng, fake)

    print("[2/7] product history and promo windows")
    prod_versions, prod_changes, promos = build_product_history(cfg, rng)

    change_dates = {date.fromisoformat(c["change_date"]) for c in dist_changes + prod_changes}
    snapshot_dates = sorted(set(cfg.snapshot_dates) | change_dates)

    print(f"[3/7] master snapshots ({len(snapshot_dates)} dates) and product API feeds")
    master_files = write_distributor_snapshots(cfg, dist_versions, snapshot_dates)
    product_feed_files = write_product_feeds(cfg, prod_versions, promos, snapshot_dates)

    first_idx = prod_versions.groupby("product_id", sort=True).head(1).index
    initial_products = prod_versions.loc[first_idx].sort_values("product_id").reset_index(drop=True)
    base_mrps = initial_products["mrp"].to_numpy(dtype=float)
    inactive_mask = (~initial_products["is_active"].astype(bool)).to_numpy()

    print("[4/7] sales CSV files (one per distributor per day)")
    catalogue = build_sales_catalogue(cfg, rng, base_mrps=base_mrps, inactive_mask=inactive_mask)
    file_index, reservoir, sales_injections = generate_sales_files(cfg, rng, catalogue)
    sales_rows = int(sum(f["row_count"] for f in file_index))
    print(f"       {len(file_index):,} files, {sales_rows:,} rows")

    reservoir.to_csv(f"{cfg.manifest_dir}/order_reservoir.csv", index=False)

    print("[5/7] late corrections (PRB6)")
    late = generate_late_corrections(cfg, rng, reservoir)

    print("[6/7] cross-file duplicates and duplicate file delivery (PRB1/PRB5)")
    cross = inject_cross_file_duplicates(cfg, rng, file_index)
    dup_files = deliver_duplicate_files(cfg, rng, file_index)

    print("[7/7] application logs (BR3)")
    log_files = generate_logs(cfg, rng)

    pd.DataFrame(file_index).to_csv(f"{cfg.manifest_dir}/sales_file_index.csv", index=False)

    manifest = {
        "generated_at_utc": pd.Timestamp.now(tz="UTC").isoformat(),
        "profile": cfg.profile,
        "seed": cfg.seed,
        "window": {
            "start_date": cfg.start_date.isoformat(),
            "end_date": cfg.end_date.isoformat(),
            "days": cfg.days,
        },
        "counts": {
            "distributors": cfg.distributor_count,
            "products": cfg.product_count,
            "sales_files": len(file_index),
            "sales_rows": sales_rows,
            "master_snapshot_files": len(master_files),
            "product_feed_files": len(product_feed_files),
            "log_files": len(log_files),
            "late_correction_files": len(late["late_correction_files"]),
            "duplicate_file_deliveries": len(dup_files),
        },
        "sales_injections": sales_injections,
        "cross_file_duplicates": cross,
        "late_corrections": late,
        "duplicate_delivered_files": dup_files,
        "scd2": {
            "distributor_change_events": dist_changes,
            "product_change_event_count": len(prod_changes),
            "product_change_events_sample": prod_changes[:20],
            "snapshot_dates_count": len(snapshot_dates),
        },
        "schema_pivot": {
            "date": PIVOT_DATE.isoformat(),
            "new_columns": SALES_EXTRA_COLUMNS,
        },
        "artifacts": {
            "sales_file_index": f"{cfg.manifest_dir}/sales_file_index.csv",
            "order_reservoir": f"{cfg.manifest_dir}/order_reservoir.csv",
        },
    }
    manifest_path = f"{cfg.manifest_dir}/manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    elapsed = time.time() - started
    print("")
    print("=== generation complete ===")
    print(f"landing      : {cfg.landing_dir}")
    print(f"manifest     : {manifest_path}")
    print(f"sales files  : {len(file_index):,}")
    print(f"sales rows   : {sales_rows:,}")
    print(f"bad rows     : {sales_injections}")
    print(f"late corr    : +{late['expected_updates_of_existing_orders']} updates, +{late['expected_inserts_of_new_orders']} inserts")
    print(f"duplicate files delivered: {len(dup_files)}")
    print(f"elapsed      : {elapsed:.1f}s")


if __name__ == "__main__":
    main()
