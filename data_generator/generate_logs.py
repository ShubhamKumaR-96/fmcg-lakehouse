"""Generate unstructured application/pipeline log files (BR3)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from data_generator.config import GeneratorConfig

LEVELS = ["INFO", "WARN", "ERROR", "DEBUG"]
LEVEL_PROBS = [0.80, 0.12, 0.04, 0.04]
COMPONENTS = [
    "bronze.ingest_sales",
    "bronze.ingest_api",
    "bronze.ingest_logs",
    "silver.validate",
    "silver.scd2",
    "gold.agg_daily",
    "workflow.scheduler",
]
MALFORMED_SAMPLES = [
    "%%% corrupt line - no timestamp %%%",
    "INFO missing-level-brackets [bronze.ingest_sales] weird",
    "",
    "\x00binary junk in log",
    "2026-03-01 incomplete",
]


def _line(rng: np.random.Generator, day: date, level: str, component: str) -> str:
    ts = f"{day.isoformat()} {int(rng.integers(0, 24)):02d}:{int(rng.integers(0, 60)):02d}:{int(rng.integers(0, 60)):02d}"
    millis = int(rng.integers(0, 1000))
    dist = f"DIST{int(rng.integers(1, 51)):03d}"
    if level == "ERROR":
        msgs = [
            f"file rejected schema check path=sales/{day.isoformat()}/sales_{dist}_{day.isoformat()}.csv reason=missing_column",
            f"merge failed target=silver.fct_sales rows={int(rng.integers(10, 500))} retry=1",
            f"api timeout url=/v1/pricing/feeds status=504 attempt={int(rng.integers(1, 4))}",
            f"delta concurrent modification target=silver.dim_distributor op=merge",
        ]
        msg = str(rng.choice(msgs))
    elif level == "WARN":
        msgs = [
            f"slow file read seconds={int(rng.integers(5, 90))} path=sales/{day.isoformat()}",
            f"null values detected column=quantity count={int(rng.integers(1, 200))}",
            f"late file detected lag_days={int(rng.integers(2, 20))}",
        ]
        msg = str(rng.choice(msgs))
    elif level == "DEBUG":
        msg = f"partition prune order_date={day.isoformat()} files={int(rng.integers(1, 60))}"
    else:
        msgs = [
            f"processed file=sales_{dist}_{day.isoformat()}.csv rows={int(rng.integers(10, 200))} status=ok",
            f"checkpoint updated path=/landing/sales files={int(rng.integers(1, 60))}",
            f"agg refresh table=gold.fct_daily_sales partitions={int(rng.integers(1, 30))} status=ok",
        ]
        msg = str(rng.choice(msgs))
    return f"{ts}.{millis:03d} {level} [{component}] {msg}"


def generate_logs(cfg: GeneratorConfig, rng: np.random.Generator) -> list[str]:
    incident_dates = set()
    while len(incident_dates) < cfg.incident_days:
        offset = int(rng.integers(0, cfg.days))
        incident_dates.add(cfg.start_date + timedelta(days=offset))

    files: list[str] = []
    d = cfg.start_date
    while d <= cfg.end_date:
        n_lines = cfg.log_lines_per_day * (5 if d in incident_dates else 1)
        levels = rng.choice(LEVELS, size=n_lines, p=LEVEL_PROBS if d not in incident_dates else [0.10, 0.15, 0.65, 0.10])
        lines = []
        for i in range(n_lines):
            if rng.random() < 0.02:
                lines.append(str(rng.choice(MALFORMED_SAMPLES)))
                continue
            component = str(rng.choice(COMPONENTS))
            lines.append(_line(rng, d, str(levels[i]), component))
        path = f"{cfg.logs_dir}/app_logs_{d.isoformat()}.log"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        files.append(path)
        d += timedelta(days=1)
    return files
