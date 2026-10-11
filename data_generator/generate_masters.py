"""Generate distributor master history and product price/promo API feeds."""
from __future__ import annotations

import bisect
import json
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
from faker import Faker

from data_generator.config import CATEGORY_BRANDS, PACK_SIZES_G, REGIONS, REGION_WEIGHTS, VARIANTS, GeneratorConfig

COMPANY_SUFFIXES = ["Traders", "Enterprises", "Distributors", "Supply Co", "Sales Corp", "Distribution House"]


def _slug(text: str, max_len: int = 24) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:max_len].strip("-") or "distributor"


def _unique_company_name(fake: Faker, existing: set[str]) -> str:
    for _ in range(50):
        base = fake.company()
        if base not in existing:
            existing.add(base)
            return base
        candidate = f"{fake.last_name()} {COMPANY_SUFFIXES[int(fake.random_int(0, len(COMPANY_SUFFIXES) - 1))]}"
        if candidate not in existing:
            existing.add(candidate)
            return candidate
    raise RuntimeError("could not generate unique company name")


def build_distributor_history(cfg: GeneratorConfig, rng: np.random.Generator, fake: Faker) -> tuple[pd.DataFrame, list[dict]]:
    region_names = list(REGION_WEIGHTS)
    region_probs = list(REGION_WEIGHTS.values())
    used_names: set[str] = set()
    rows: list[dict] = []
    change_log: list[dict] = []

    region_change_ids = set(
        rng.choice(cfg.distributor_count, size=max(1, int(cfg.distributor_count * cfg.pct_distributors_with_region_change)), replace=False).tolist()
    )
    minor_change_ids = set(
        rng.choice(cfg.distributor_count, size=max(1, int(cfg.distributor_count * cfg.pct_distributors_with_minor_change)), replace=False).tolist()
    )

    for i in range(cfg.distributor_count):
        dist_id = f"DIST{i + 1:03d}"
        region = str(rng.choice(region_names, p=region_probs))
        state = str(rng.choice(REGIONS[region]))
        name = _unique_company_name(fake, used_names)
        phone = f"+91 9{rng.integers(10000000, 99999999)}"
        email = f"{_slug(name)}.{i + 1:03d}@example.com"
        credit_limit = int(rng.integers(50, 5001) * 100)
        initial_start = cfg.start_date - timedelta(days=int(rng.integers(1, 400)))
        rows.append(
            {
                "distributor_id": dist_id,
                "version_start": initial_start,
                "name": name,
                "region": region,
                "state": state,
                "phone": phone,
                "email": email,
                "credit_limit": credit_limit,
                "change_reason": "initial_load",
            }
        )

        if i in region_change_ids:
            change_date = cfg.start_date + timedelta(days=int(rng.integers(cfg.days // 4, cfg.days // 2)))
            new_region = str(rng.choice([r for r in region_names if r != region]))
            new_state = str(rng.choice(REGIONS[new_region]))
            rows.append(
                {
                    "distributor_id": dist_id,
                    "version_start": change_date,
                    "name": name,
                    "region": new_region,
                    "state": new_state,
                    "phone": phone,
                    "email": email,
                    "credit_limit": credit_limit,
                    "change_reason": "region_reassignment",
                }
            )
            change_log.append(
                {
                    "distributor_id": dist_id,
                    "change_date": change_date.isoformat(),
                    "changed_fields": ["region", "state"],
                    "old": {"region": region, "state": state},
                    "new": {"region": new_region, "state": new_state},
                    "problem_id": "PRB7",
                }
            )
            region = new_region

        if i in minor_change_ids:
            n_minor = int(rng.integers(1, 3))
            last_start = rows[-1]["version_start"]
            for _ in range(n_minor):
                change_date = min(last_start + timedelta(days=int(rng.integers(30, 150))), cfg.end_date - timedelta(days=1))
                if change_date <= last_start:
                    continue
                last_start = change_date
                field_choice = str(rng.choice(["credit_limit", "phone_email", "name"]))
                new_row = dict(rows[-1])
                new_row["version_start"] = change_date
                new_row["change_reason"] = f"minor_{field_choice}"
                old_snapshot: dict = {}
                new_snapshot: dict = {}
                if field_choice == "credit_limit":
                    old_snapshot["credit_limit"] = rows[-1]["credit_limit"]
                    new_limit = int(rng.integers(50, 5001) * 100)
                    new_row["credit_limit"] = new_limit
                    new_snapshot["credit_limit"] = new_limit
                elif field_choice == "phone_email":
                    old_snapshot["phone"] = rows[-1]["phone"]
                    old_snapshot["email"] = rows[-1]["email"]
                    new_phone = f"+91 9{rng.integers(10000000, 99999999)}"
                    new_email = f"{_slug(name)}.{i + 1:03d}.{change_date.strftime('%y%m')}@example.com"
                    new_row["phone"] = new_phone
                    new_row["email"] = new_email
                    new_snapshot["phone"] = new_phone
                    new_snapshot["email"] = new_email
                else:
                    old_snapshot["name"] = rows[-1]["name"]
                    new_name = f"{rows[-1]['name']} {COMPANY_SUFFIXES[int(rng.integers(0, len(COMPANY_SUFFIXES)))]}"
                    new_row["name"] = new_name
                    new_snapshot["name"] = new_name
                rows.append(new_row)
                change_log.append(
                    {
                        "distributor_id": dist_id,
                        "change_date": change_date.isoformat(),
                        "changed_fields": [field_choice],
                        "old": old_snapshot,
                        "new": new_snapshot,
                        "problem_id": "SCD2_MINOR",
                    }
                )

    versions = pd.DataFrame(rows).sort_values(["distributor_id", "version_start"]).reset_index(drop=True)
    versions["version_start"] = pd.to_datetime(versions["version_start"])
    return versions, change_log


def build_product_history(cfg: GeneratorConfig, rng: np.random.Generator) -> tuple[pd.DataFrame, list[dict], list[dict]]:
    category_names = list(CATEGORY_BRANDS)
    rows: list[dict] = []
    change_log: list[dict] = []
    promos: list[dict] = []

    price_change_ids = set(
        rng.choice(cfg.product_count, size=max(1, int(cfg.product_count * cfg.pct_products_with_price_change)), replace=False).tolist()
    )
    status_change_ids = set(
        rng.choice(cfg.product_count, size=max(1, int(cfg.product_count * cfg.pct_products_with_status_change)), replace=False).tolist()
    )
    inactive_ids = set(
        rng.choice(cfg.product_count, size=max(1, int(cfg.product_count * cfg.pct_products_inactive_from_start)), replace=False).tolist()
    )

    for i in range(cfg.product_count):
        product_id = f"PROD{i + 1:04d}"
        category = category_names[i % len(category_names)]
        brand = str(rng.choice(CATEGORY_BRANDS[category]))
        variant = VARIANTS[int(rng.integers(0, len(VARIANTS)))]
        pack = PACK_SIZES_G[int(rng.integers(0, len(PACK_SIZES_G)))]
        product_name = f"{brand} {category.split()[0]} {variant} {pack}g"
        mrp = float(rng.integers(10, 800) + 0.5)
        is_active = False if i in inactive_ids else True
        initial_start = cfg.start_date - timedelta(days=int(rng.integers(1, 500)))
        rows.append(
            {
                "product_id": product_id,
                "version_start": initial_start,
                "product_name": product_name,
                "category": category,
                "brand": brand,
                "mrp": mrp,
                "pack_size": pack,
                "is_active": is_active,
                "change_reason": "initial_load",
            }
        )

        if i in price_change_ids:
            change_date = cfg.start_date + timedelta(days=int(rng.integers(cfg.days // 5, (cfg.days * 4) // 5)))
            factor = float(rng.uniform(0.85, 1.15))
            new_mrp = round(mrp * factor, 2)
            rows.append(
                {
                    "product_id": product_id,
                    "version_start": change_date,
                    "product_name": product_name,
                    "category": category,
                    "brand": brand,
                    "mrp": new_mrp,
                    "pack_size": pack,
                    "is_active": is_active,
                    "change_reason": "price_revision",
                }
            )
            change_log.append(
                {
                    "product_id": product_id,
                    "change_date": change_date.isoformat(),
                    "changed_fields": ["mrp"],
                    "old": {"mrp": mrp},
                    "new": {"mrp": new_mrp},
                }
            )

        if i in status_change_ids:
            base_start = rows[-1]["version_start"]
            change_date = min(base_start + timedelta(days=int(rng.integers(45, 200))), cfg.end_date - timedelta(days=1))
            if change_date > base_start:
                new_active = not rows[-1]["is_active"]
                rows.append(
                    {
                        "product_id": product_id,
                        "version_start": change_date,
                        "product_name": product_name,
                        "category": category,
                        "brand": brand,
                        "mrp": rows[-1]["mrp"],
                        "pack_size": pack,
                        "is_active": new_active,
                        "change_reason": "lifecycle_change",
                    }
                )
                change_log.append(
                    {
                        "product_id": product_id,
                        "change_date": change_date.isoformat(),
                        "changed_fields": ["is_active"],
                        "old": {"is_active": not new_active},
                        "new": {"is_active": new_active},
                    }
                )

        if rng.random() < 0.35:
            promo_start = cfg.start_date + timedelta(days=int(rng.integers(0, max(1, cfg.days - 30))))
            promo_len = int(rng.integers(7, 45))
            promos.append(
                {
                    "product_id": product_id,
                    "promo_pct": float(rng.choice([5.0, 10.0, 15.0, 20.0, 25.0])),
                    "promo_start_date": promo_start.isoformat(),
                    "promo_end_date": (promo_start + timedelta(days=promo_len)).isoformat(),
                }
            )

    versions = pd.DataFrame(rows).sort_values(["product_id", "version_start"]).reset_index(drop=True)
    versions["version_start"] = pd.to_datetime(versions["version_start"])
    return versions, change_log, promos


def _asof_version(entity_versions: pd.DataFrame, key_col: str, key: str, as_of: date) -> dict:
    sub = entity_versions[entity_versions[key_col] == key]
    starts = list(sub["version_start"])
    idx = bisect.bisect_right(starts, pd.Timestamp(as_of)) - 1
    if idx < 0:
        idx = 0
    return sub.iloc[idx].to_dict()


def write_distributor_snapshots(cfg: GeneratorConfig, versions: pd.DataFrame, snapshot_dates: list[date]) -> list[str]:
    ids = sorted(versions["distributor_id"].unique())
    files: list[str] = []
    for d in snapshot_dates:
        records = []
        for dist_id in ids:
            v = _asof_version(versions, "distributor_id", dist_id, d)
            records.append(
                {
                    "distributor_id": dist_id,
                    "name": str(v["name"]),
                    "region": str(v["region"]),
                    "state": str(v["state"]),
                    "phone": str(v["phone"]),
                    "email": str(v["email"]),
                    "credit_limit": int(v["credit_limit"]),
                    "effective_date": v["version_start"].date().isoformat(),
                    "updated_at": f"{d.isoformat()} 06:00:00",
                }
            )
        df = pd.DataFrame(records)
        path = f"{cfg.masters_dir}/master_{d.isoformat()}.csv"
        df.to_csv(path, index=False)
        files.append(path)
    return files


def write_product_feeds(
    cfg: GeneratorConfig,
    versions: pd.DataFrame,
    promos: list[dict],
    snapshot_dates: list[date],
) -> list[str]:
    ids = sorted(versions["product_id"].unique())
    promos_by_product: dict[str, list[dict]] = {}
    for p in promos:
        promos_by_product.setdefault(p["product_id"], []).append(p)

    files: list[str] = []
    for d in snapshot_dates:
        records = []
        for product_id in ids:
            v = _asof_version(versions, "product_id", product_id, d)
            active_promo = None
            for p in promos_by_product.get(product_id, []):
                if p["promo_start_date"] <= d.isoformat() <= p["promo_end_date"]:
                    active_promo = p
                    break
            records.append(
                {
                    "product_id": product_id,
                    "product_name": str(v["product_name"]),
                    "category": str(v["category"]),
                    "brand": str(v["brand"]),
                    "mrp": float(v["mrp"]),
                    "pack_size": int(v["pack_size"]),
                    "is_active": bool(v["is_active"]),
                    "price_effective_date": v["version_start"].date().isoformat(),
                    "promo_pct": float(active_promo["promo_pct"]) if active_promo else 0.0,
                    "promo_start_date": active_promo["promo_start_date"] if active_promo else None,
                    "promo_end_date": active_promo["promo_end_date"] if active_promo else None,
                }
            )
        payload = {
            "fetch_id": f"FETCH-{d.strftime('%Y%m%d')}-0001",
            "source_system": "novafresh-pricing-api",
            "fetched_at": f"{d.isoformat()}T07:30:00Z",
            "record_count": len(records),
            "records": records,
        }
        path = f"{cfg.api_dir}/product_feed_{d.isoformat()}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        files.append(path)
    return files
