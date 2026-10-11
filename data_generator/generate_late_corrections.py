"""Late-arriving and corrected records (PRB6)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from data_generator.config import ORDER_STATUS, SALES_ALL_COLUMNS, GeneratorConfig


def generate_late_corrections(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    reservoir: pd.DataFrame,
) -> dict:
    if reservoir.empty:
        raise ValueError("reservoir is empty - cannot generate late corrections")

    res = reservoir.copy()
    res["order_date"] = pd.to_datetime(res["order_date"])
    res["updated_at"] = pd.to_datetime(res["updated_at"])
    res = res[res["quantity"].notna() & (pd.to_numeric(res["quantity"], errors="coerce") > 0)]
    res = res[~res["distributor_id"].astype(str).str.startswith("UNKNOWN")]
    res = res[~res["product_id"].astype(str).str.startswith("PRODUNKNOWN")]
    res = res[pd.to_datetime(res["order_date"]).dt.date <= cfg.end_date]
    cutoff = cfg.start_date + timedelta(days=int(cfg.days * 0.6))
    eligible = res[res["order_date"].dt.date <= cutoff]
    if eligible.empty:
        eligible = res

    n_corr = min(cfg.late_correction_orders, len(eligible))
    chosen = eligible.sample(n=n_corr, random_state=int(rng.integers(0, 1_000_000))).copy()
    chosen["order_date"] = chosen["order_date"].dt.strftime("%Y-%m-%d")
    chosen["updated_at"] = chosen["updated_at"].dt.strftime("%Y-%m-%d %H:%M:%S")

    n = len(chosen)
    flip_status = rng.random(n) < 0.40
    change_qty = rng.random(n) < 0.70
    qty = pd.to_numeric(chosen["quantity"], errors="coerce").fillna(1)
    chosen.loc[change_qty, "quantity"] = (qty[change_qty] + rng.integers(1, 6)).astype(float)
    chosen.loc[flip_status, "order_status"] = "returned"
    delay_days = rng.integers(3, 11, n)
    chosen["updated_at"] = (
        pd.to_datetime(chosen["updated_at"]) + pd.to_timedelta(delay_days, unit="D")
    ).dt.strftime("%Y-%m-%d %H:%M:%S")

    new_rows = []
    base_date = pd.Timestamp(cfg.start_date)
    for i in range(cfg.late_new_orders):
        order_offset = int(rng.integers(0, max(1, int(cfg.days * 0.7))))
        order_day = (base_date + pd.Timedelta(days=order_offset)).strftime("%Y-%m-%d")
        src = eligible.iloc[int(rng.integers(0, len(eligible)))]
        src_price = pd.to_numeric(src["unit_price"], errors="coerce")
        unit_price = 50.0 if pd.isna(src_price) or float(src_price) <= 0 else round(float(src_price), 2)
        new_rows.append(
            {
                "order_id": f"ORDLATE{i + 1:06d}",
                "order_date": order_day,
                "distributor_id": str(src["distributor_id"]),
                "outlet_id": str(src["outlet_id"]),
                "product_id": str(src["product_id"]),
                "quantity": float(rng.integers(1, 10)),
                "unit_price": unit_price,
                "discount_pct": 0.0,
                "order_status": str(rng.choice(list(ORDER_STATUS), p=list(ORDER_STATUS.values()))),
                "updated_at": (pd.Timestamp(order_day) + pd.Timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),
                "route_code": str(src.get("route_code", "R00")),
                "salesman_code": str(src.get("salesman_code", "SM000")),
            }
        )
    new_df = pd.DataFrame(new_rows)

    combined = pd.concat([chosen, new_df], ignore_index=True)
    file_ts = pd.to_datetime(combined["order_date"]) + pd.Timedelta(days=14)
    file_ts = file_ts.clip(upper=pd.Timestamp(cfg.end_date))
    combined["file_date"] = file_ts.dt.date

    files: list[str] = []
    for i, (file_date, group) in enumerate(sorted(combined.groupby("file_date")), start=1):
        out = group.drop(columns=["file_date"])[[c for c in SALES_ALL_COLUMNS if c in group.columns]]
        day_str = file_date.isoformat()
        path = f"{cfg.sales_dir}/{day_str}/late_corr_{day_str}_{i:03d}.csv"
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(path, index=False)
        files.append(path)

    return {
        "late_correction_files": files,
        "expected_updates_of_existing_orders": int(n_corr),
        "expected_inserts_of_new_orders": int(cfg.late_new_orders),
        "corrected_order_ids_sample": chosen["order_id"].head(10).tolist(),
    }
