"""Cross-file duplicate rows and duplicate file re-delivery (PRB1 tail, PRB5)."""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from data_generator.config import GeneratorConfig


def inject_cross_file_duplicates(
    cfg: GeneratorConfig,
    rng: np.random.Generator,
    file_index: list[dict],
) -> dict:
    paths = np.array([f["path"] for f in file_index])
    modified_ids: list[str] = []
    plain_ids: list[str] = []

    for _ in range(cfg.cross_file_duplicate_pairs):
        src_i, tgt_i = rng.choice(len(paths), size=2, replace=False)
        src_path, tgt_path = paths[src_i], paths[tgt_i]
        src = pd.read_csv(src_path, dtype=str)
        if src.empty:
            continue
        n_rows = int(rng.integers(1, 4))
        chosen = src.sample(n=min(n_rows, len(src)), random_state=int(rng.integers(0, 1_000_000)))
        payload = chosen.copy()
        if rng.random() < 0.5:
            payload["quantity"] = (pd.to_numeric(payload["quantity"], errors="coerce").fillna(1) + rng.integers(1, 4)).astype(str)
            payload["updated_at"] = (pd.to_datetime(payload["updated_at"]) + pd.Timedelta(days=2)).dt.strftime("%Y-%m-%d %H:%M:%S")
            modified_ids.extend(payload["order_id"].tolist())
        else:
            plain_ids.extend(payload["order_id"].tolist())

        tgt = pd.read_csv(tgt_path, dtype=str)
        merged = pd.concat([tgt, payload], ignore_index=True)
        merged.to_csv(tgt_path, index=False)

    return {
        "cross_file_duplicate_pairs": cfg.cross_file_duplicate_pairs,
        "cross_file_dup_rows_modified": len(modified_ids),
        "cross_file_dup_rows_plain": len(plain_ids),
        "cross_file_dup_order_ids_sample": (modified_ids + plain_ids)[:20],
    }


def deliver_duplicate_files(cfg: GeneratorConfig, rng: np.random.Generator, file_index: list[dict]) -> list[str]:
    candidates = [f for f in file_index if f["row_count"] > 5]
    picks = rng.choice(len(candidates), size=min(cfg.duplicate_file_deliveries, len(candidates)), replace=False)
    copies: list[str] = []
    for i in np.atleast_1d(picks):
        src = candidates[int(i)]["path"]
        p = Path(src)
        copy_path = p.with_name(f"{p.stem}_copy{int(rng.integers(1, 99))}{p.suffix}")
        shutil.copyfile(src, copy_path)
        copies.append(str(copy_path))
    return copies
