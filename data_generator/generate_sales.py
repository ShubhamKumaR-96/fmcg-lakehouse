"""Generate daily distributor sales CSV files with injected data problems."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from data_generator.config import (
    DISCOUNT_CHOICES,
    DISCOUNT_PROBS,
    INJECTION_RATES,
    ORDER_STATUS,
    PIVOT_DATE,
    QUANTITY_CHOICES,
    QUANTITY_PROBS,
    SALES_ALL_COLUMNS,
    SALES_BASE_COLUMNS,
    GeneratorConfig,
)

UNKNOWN_DISTRIBUTOR = "UNKNOWN_DIST_999"
UNKNOWN_PRODUCT = "PRODUNKNOWN9999"


def build_sales_catalogue(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    base_mrps: np.ndarray | None = None,
    inactive_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    product_ids = np.array([f"PROD{i + 1:04d}" for i in range(cfg.product_count)])
    mrps = base_mrps if base_mrps is not None else rng.uniform(10, 800, cfg.product_count).round(2)
    ranks = np.arange(1, cfg.product_count + 1)
    weights = 1.0 / ranks**0.9
    if inactive_mask is not None:
        weights[inactive_mask] = 0.0
    weights = weights / weights.sum()
    return product_ids, mrps, weights


def _take_disjoint(permutation: np.ndarray, cursor: int, rate: float) -> tuple[np.ndarray, int]:
    k = int(permutation.size * rate)
    idx = permutation[cursor : cursor + k]
    return idx, cursor + k


def _apply_row_injections(cfg: GeneratorConfig, rng: np.random.Generator, df: pd.DataFrame) -> dict[str, int]:
    n = len(df)
    perm = rng.permutation(n)
    cursor = 0
    counts: dict[str, int] = {}

    idx, cursor = _take_disjoint(perm, cursor, INJECTION_RATES["quantity_negative"])
    df.loc[idx, "quantity"] = -np.abs(df.loc[idx, "quantity"].to_numpy())
    counts["quantity_negative"] = int(idx.size)

    idx, cursor = _take_disjoint(perm, cursor, INJECTION_RATES["quantity_null"])
    df.loc[idx, "quantity"] = np.nan
    counts["quantity_null"] = int(idx.size)

    idx, cursor = _take_disjoint(perm, cursor, INJECTION_RATES["unknown_distributor"])
    df.loc[idx, "distributor_id"] = UNKNOWN_DISTRIBUTOR
    counts["unknown_distributor"] = int(idx.size)

    idx, cursor = _take_disjoint(perm, cursor, INJECTION_RATES["unknown_product"])
    df.loc[idx, "product_id"] = UNKNOWN_PRODUCT
    counts["unknown_product"] = int(idx.size)

    idx, cursor = _take_disjoint(perm, cursor, INJECTION_RATES["future_order_date"])
    offsets = rng.integers(1, 31, idx.size)
    df.loc[idx, "order_date"] = (pd.Timestamp(cfg.end_date) + pd.to_timedelta(offsets, unit="D")).to_numpy()
    counts["future_order_date"] = int(idx.size)

    idx, cursor = _take_disjoint(perm, cursor, INJECTION_RATES["discount_out_of_range"])
    df.loc[idx, "discount_pct"] = rng.choice([-10.0, 120.0, 150.0], idx.size)
    counts["discount_out_of_range"] = int(idx.size)

    idx, cursor = _take_disjoint(perm, cursor, INJECTION_RATES["unit_price_invalid"])
    df.loc[idx, "unit_price"] = rng.choice([0.0, -1.0], idx.size)
    counts["unit_price_invalid"] = int(idx.size)

    return counts


def _build_distributor_frame(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    dist_id: str,
    dist_seq: int,
    product_ids: np.ndarray,
    product_mrps: np.ndarray,
    product_weights: np.ndarray,
    dates: np.ndarray,
) -> pd.DataFrame:
    n_days = dates.size
    daily_counts = rng.poisson(cfg.avg_rows_per_dist_day, n_days)
    total = int(daily_counts.sum())
    order_dates = np.repeat(dates, daily_counts)

    product_idx = rng.choice(product_ids.size, size=total, p=product_weights)
    pid = product_ids[product_idx]
    mrp = product_mrps[product_idx]

    quantity = rng.choice(QUANTITY_CHOICES, size=total, p=QUANTITY_PROBS).astype(float)
    day_offset = (order_dates - dates[0]).astype("timedelta64[D]").astype(int)
    price_trend = 1.0 + 0.0004 * day_offset
    unit_price = np.round(mrp * rng.uniform(0.65, 0.85, total) * price_trend, 2)
    discount_pct = rng.choice(DISCOUNT_CHOICES, size=total, p=DISCOUNT_PROBS).astype(float)

    status_values = list(ORDER_STATUS)
    status_probs = list(ORDER_STATUS.values())
    order_status = rng.choice(status_values, size=total, p=status_probs)

    hours = rng.integers(1, 73, total)
    updated_at = order_dates.astype("datetime64[s]") + hours.astype("timedelta64[h]")

    n_outlets = int(rng.integers(cfg.outlets_min, cfg.outlets_max + 1))
    outlet_ids = np.array([f"OUT{dist_seq:03d}{i:04d}" for i in range(n_outlets)])
    route_codes = np.array([f"R{i % 12:02d}" for i in range(n_outlets)])
    salesman_codes = np.array([f"SM{i % 30:03d}" for i in range(n_outlets)])
    outlet_pos = rng.integers(0, n_outlets, total)

    df = pd.DataFrame(
        {
            "order_date": pd.to_datetime(order_dates),
            "distributor_id": np.full(total, dist_id, dtype=object),
            "outlet_id": outlet_ids[outlet_pos],
            "product_id": pid,
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_pct": discount_pct,
            "order_status": order_status,
            "updated_at": pd.to_datetime(updated_at),
            "route_code": route_codes[outlet_pos],
            "salesman_code": salesman_codes[outlet_pos],
        }
    )
    df["order_seq"] = df.groupby("order_date", sort=False).cumcount()
    df["order_id"] = (
        "ORD" + dist_id[-3:] + df["order_date"].dt.strftime("%Y%m%d") + df["order_seq"].map("{:05d}".format)
    )
    return df.drop(columns=["order_seq"])


def _add_within_file_duplicates(rng: np.random.Generator, day_frames: dict[date, pd.DataFrame], target_rows: int) -> int:
    all_keys = list(day_frames)
    added = 0
    attempts = 0
    while added < target_rows and attempts < target_rows * 3:
        attempts += 1
        src_key = all_keys[int(rng.integers(0, len(all_keys)))]
        src = day_frames[src_key]
        if src.empty:
            continue
        row_idx = int(rng.integers(0, len(src)))
        dup = src.iloc[[row_idx]].copy()
        if rng.random() < 0.3:
            dup["quantity"] = dup["quantity"].fillna(1) + rng.integers(1, 5)
            dup["updated_at"] = dup["updated_at"] + pd.Timedelta(hours=int(rng.integers(2, 24)))
        day_frames[src_key] = pd.concat([src, dup], ignore_index=True)
        added += 1
    return added


def _write_day_file(cfg: GeneratorConfig, dist_id: str, day: date, frame: pd.DataFrame) -> str:
    day_str = day.isoformat()
    out = frame.copy()
    if day < PIVOT_DATE:
        out = out[[c for c in SALES_BASE_COLUMNS if c in out.columns]]
    else:
        out = out[[c for c in SALES_ALL_COLUMNS if c in out.columns]]
    out["order_date"] = pd.to_datetime(out["order_date"]).dt.strftime("%Y-%m-%d")
    out["updated_at"] = pd.to_datetime(out["updated_at"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    path = f"{cfg.sales_dir}/{day_str}/sales_{dist_id}_{day_str}.csv"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    return path


def generate_sales_files(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    catalogue: tuple[np.ndarray, np.ndarray, np.ndarray],
) -> tuple[list[dict], pd.DataFrame, dict[str, int]]:
    product_ids, product_mrps, product_weights = catalogue
    dates = np.arange(
        np.datetime64(cfg.start_date),
        np.datetime64(cfg.end_date) + np.timedelta64(1, "D"),
        dtype="datetime64[D]",
    )

    file_index: list[dict] = []
    injection_counts: dict[str, int] = {
        "quantity_negative": 0,
        "quantity_null": 0,
        "unknown_distributor": 0,
        "unknown_product": 0,
        "future_order_date": 0,
        "discount_out_of_range": 0,
        "unit_price_invalid": 0,
        "duplicate_within_file": 0,
    }
    reservoir_rows: list[dict] = []

    for dist_seq in range(1, cfg.distributor_count + 1):
        dist_id = f"DIST{dist_seq:03d}"
        frame = _build_distributor_frame(cfg, rng, dist_id, dist_seq, product_ids, product_mrps, product_weights, dates)
        counts = _apply_row_injections(cfg, rng, frame)
        for k, v in counts.items():
            injection_counts[k] += v

        day_map: dict[date, pd.DataFrame] = {d: g.copy() for d, g in frame.groupby(frame["order_date"].dt.date)}

        target_dups = int(len(frame) * INJECTION_RATES["duplicate_within_file"])
        added = _add_within_file_duplicates(rng, day_map, target_dups)
        injection_counts["duplicate_within_file"] += added

        for day, day_df in sorted(day_map.items()):
            path = _write_day_file(cfg, dist_id, day, day_df)
            file_index.append(
                {
                    "path": path,
                    "distributor_id": dist_id,
                    "file_date": day.isoformat(),
                    "row_count": int(len(day_df)),
                    "has_extra_columns": day >= PIVOT_DATE,
                }
            )

        if len(reservoir_rows) < cfg.reservoir_sample_size:
            per_dist_target = max(1, cfg.reservoir_sample_size // cfg.distributor_count)
            step = max(1, len(frame) // per_dist_target)
            sample = frame.iloc[::step].head(per_dist_target)
            reservoir_rows.extend(sample.to_dict("records"))

    reservoir = pd.DataFrame(reservoir_rows)
    injection_counts["reservoir_rows"] = int(len(reservoir))
    return file_index, reservoir, injection_counts
