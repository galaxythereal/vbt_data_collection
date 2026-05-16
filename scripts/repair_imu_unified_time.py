#!/usr/bin/env python3
"""Repair non-monotonic raw_imu.csv unified_time_s using ESP device time."""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


def repair_csv(path: Path, dry_run: bool) -> tuple[bool, str]:
    try:
        df = pd.read_csv(path)
    except Exception as e:
        return False, f"read error: {e}"
    if "unified_time_s" not in df or "esp_timestamp_us" not in df:
        return False, "missing time columns"
    t = df["unified_time_s"].to_numpy(float)
    esp = df["esp_timestamp_us"].to_numpy(float) * 1e-6
    if len(t) < 2 or not np.any(t > 1e9):
        return False, "not wall-clock unified time"
    dt = np.diff(t)
    bad = (dt <= 0) | (dt > 0.005)
    if not np.any(bad):
        return False, "already monotonic"
    offset = float(np.nanmedian(t - esp))
    repaired = esp + offset
    rdt = np.diff(repaired)
    if np.any(rdt <= 0) or np.any(rdt > 0.020):
        return False, "ESP clock is not clean enough to repair safely"
    if not dry_run:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"raw_imu.before_time_repair_{stamp}.csv")
        shutil.copy2(path, backup)
        df["unified_time_s"] = repaired
        df.to_csv(path, index=False)
    return True, f"repaired {int(np.sum(bad))} bad dt samples"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default="datasets/sessions")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = Path(args.root)
    total = fixed = 0
    for p in sorted(root.glob("session_*/imu/raw_imu.csv")):
        total += 1
        ok, msg = repair_csv(p, args.dry_run)
        fixed += int(ok)
        print(f"{p.parent.parent.name}: {msg}")
    print(f"{'Would repair' if args.dry_run else 'Repaired'} {fixed}/{total} raw_imu.csv files")


if __name__ == "__main__":
    main()
