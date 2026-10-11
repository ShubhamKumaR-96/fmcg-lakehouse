"""Configuration for the NovaFresh synthetic data generator.

Everything is fictional. All names, brands, phone numbers and emails are
synthetic; email domain uses the reserved `example.com` TLD.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

REGIONS: dict[str, list[str]] = {
    "North": ["Uttar Pradesh", "Punjab", "Haryana", "Rajasthan", "Delhi"],
    "South": ["Tamil Nadu", "Karnataka", "Kerala", "Telangana", "Andhra Pradesh"],
    "East": ["West Bengal", "Bihar", "Odisha", "Jharkhand"],
    "West": ["Maharashtra", "Gujarat", "Goa", "Madhya Pradesh"],
}

REGION_WEIGHTS: dict[str, float] = {"North": 0.30, "West": 0.28, "South": 0.25, "East": 0.17}

CATEGORY_BRANDS: dict[str, list[str]] = {
    "Home Care": ["NovaClean", "SparklHome"],
    "Personal Care": ["AromaVeda", "Zesta"],
    "Food & Beverage": ["TastyBite", "Shudh"],
    "Packaged Foods": ["Zesta", "TastyBite"],
}

VARIANTS: list[str] = [
    "Original", "Lemon", "Lime", "Neem", "Aloe", "Herbal", "Classic", "Premium",
    "Family Pack", "Economy", "Fresh", "Gold", "Active", "Sensitive", "Cool",
]

PACK_SIZES_G: list[int] = [50, 100, 150, 200, 250, 500, 750, 1000]

ORDER_STATUS: dict[str, float] = {"delivered": 0.92, "returned": 0.05, "cancelled": 0.03}

SALES_BASE_COLUMNS: list[str] = [
    "order_id",
    "order_date",
    "distributor_id",
    "outlet_id",
    "product_id",
    "quantity",
    "unit_price",
    "discount_pct",
    "order_status",
    "updated_at",
]

SALES_EXTRA_COLUMNS: list[str] = ["route_code", "salesman_code"]

SALES_ALL_COLUMNS: list[str] = SALES_BASE_COLUMNS + SALES_EXTRA_COLUMNS

QUANTITY_CHOICES: list[int] = [1, 2, 3, 4, 5, 6, 8, 10, 12]
QUANTITY_PROBS: list[float] = [0.28, 0.22, 0.16, 0.12, 0.09, 0.06, 0.03, 0.02, 0.02]

DISCOUNT_CHOICES: list[float] = [0.0, 5.0, 10.0, 15.0, 20.0]
DISCOUNT_PROBS: list[float] = [0.55, 0.15, 0.14, 0.10, 0.06]

INJECTION_RATES: dict[str, float] = {
    "quantity_negative": 0.006,
    "quantity_null": 0.004,
    "unknown_distributor": 0.003,
    "unknown_product": 0.003,
    "future_order_date": 0.002,
    "discount_out_of_range": 0.005,
    "unit_price_invalid": 0.002,
    "duplicate_within_file": 0.012,
    "duplicate_across_files": 0.008,
}

PIVOT_DATE: date = date(2026, 6, 1)
PIVOT_COLUMN_NOTE = "route_code and salesman_code added to files on/after pivot date (PRB8)"


@dataclass(frozen=True)
class GeneratorConfig:
    profile: str = "dev"
    seed: int = 42
    output_dir: str = "data_generator/output"
    distributor_count: int = 50
    start_date: date = date(2026, 1, 1)
    end_date: date = date(2026, 9, 30)
    avg_rows_per_dist_day: float = 30.0
    product_count: int = 500
    outlets_min: int = 30
    outlets_max: int = 120
    pct_distributors_with_region_change: float = 0.06
    pct_distributors_with_minor_change: float = 0.10
    pct_products_with_price_change: float = 0.12
    pct_products_with_status_change: float = 0.04
    pct_products_inactive_from_start: float = 0.02
    master_snapshot_interval_days: int = 7
    duplicate_file_deliveries: int = 15
    cross_file_duplicate_pairs: int = 200
    late_correction_orders: int = 300
    late_new_orders: int = 50
    log_lines_per_day: int = 200
    incident_days: int = 5
    reservoir_sample_size: int = 5000

    @property
    def landing_dir(self) -> str:
        return f"{self.output_dir}/landing"

    @property
    def manifest_dir(self) -> str:
        return f"{self.output_dir}/manifest"

    @property
    def sales_dir(self) -> str:
        return f"{self.landing_dir}/sales"

    @property
    def masters_dir(self) -> str:
        return f"{self.landing_dir}/masters/distributor"

    @property
    def api_dir(self) -> str:
        return f"{self.landing_dir}/api/products"

    @property
    def logs_dir(self) -> str:
        return f"{self.landing_dir}/logs"

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    @property
    def snapshot_dates(self) -> list[date]:
        dates = []
        d = self.start_date
        while d <= self.end_date:
            dates.append(d)
            d += timedelta(days=self.master_snapshot_interval_days)
        if dates[-1] != self.end_date:
            dates.append(self.end_date)
        return dates


PROFILES: dict[str, dict] = {
    "dev": {
        "distributor_count": 50,
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 9, 30),
        "avg_rows_per_dist_day": 30.0,
        "product_count": 500,
    },
    "full": {
        "distributor_count": 200,
        "start_date": date(2024, 10, 1),
        "end_date": date(2026, 9, 30),
        "avg_rows_per_dist_day": 15.0,
        "product_count": 500,
    },
}


def build_config(
    profile: str = "dev",
    seed: int = 42,
    output_dir: str = "data_generator/output",
    start_date: date | None = None,
    end_date: date | None = None,
    distributor_count: int | None = None,
    avg_rows_per_dist_day: float | None = None,
) -> GeneratorConfig:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile '{profile}'. Choose from {sorted(PROFILES)}")
    p = dict(PROFILES[profile])
    if start_date is not None:
        p["start_date"] = start_date
    if end_date is not None:
        p["end_date"] = end_date
    if distributor_count is not None:
        p["distributor_count"] = distributor_count
    if avg_rows_per_dist_day is not None:
        p["avg_rows_per_dist_day"] = avg_rows_per_dist_day
    if p["start_date"] > p["end_date"]:
        raise ValueError("start_date must be on or before end_date")
    return GeneratorConfig(profile=profile, seed=seed, output_dir=output_dir, **p)
